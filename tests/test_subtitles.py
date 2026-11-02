import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import database
from backend.summarizer import VideoSummarizer


@pytest.fixture
def translation_account():
    database.init_db()
    user_id = database.create_user("translation@example.com", "unused-test-hash")["id"]
    subtitle = {"has_subtitle": True, "language": "en", "source": "yt-dlp", "segments": [
        {"start": 0, "end": 1.25, "text": "Hello"},
        {"start": 1.25, "end": 3.14159, "text": "World"},
    ], "full_text": "Hello World"}
    with database.get_db() as db:
        database.save_summary_result(db, user_id, {
            "url": "https://example.com/video", "language": "zh", "subtitle": subtitle,
            "summary": "summary", "mindmap": "# map",
        }, 1)
        source_id = db.execute("SELECT result_id FROM summary_results WHERE user_id=?", (user_id,)).fetchone()[0]
    return user_id, source_id, subtitle


def start_translation(user_id, source_id, key, force=False):
    from backend import subtitles
    response = subtitles.submit_translation(user_id, "summary", source_id, "zh", key, force)
    if not response["cached"]:
        with database.get_db() as db:
            db.execute("UPDATE tasks SET state='running',started_at=? WHERE id=? AND state='queued'", (int(time()), response["task"]["id"]))
    return response


def install_translation(model):
    def translate(**kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=json.dumps({"segments": [{"id": segment["id"], "text": "译文" + str(segment["id"])}
                                            for segment in reversed(payload["segments"])]})))])
    model.create.side_effect = translate


def test_translation_preserves_every_timestamp_caches_and_keeps_previous_on_force_failure(translation_account, model):
    from backend import extension_quota, subtitles, tasks
    user_id, source_id, original = translation_account
    install_translation(model)
    submitted = Barrier(2)
    def concurrent_submit(index):
        submitted.wait()
        return subtitles.submit_translation(user_id, "summary", source_id, "zh", f"concurrent-{index}", bool(index))
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, duplicate = list(executor.map(concurrent_submit, (0, 1)))
    assert first["task"]["id"] == duplicate["task"]["id"]
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE kind='translation'").fetchone()[0] == 1
        db.execute("UPDATE tasks SET state='running',started_at=? WHERE id=?", (int(time()), first["task"]["id"]))
    assert extension_quota.get_extension_quota(user_id)["translation"]["reserved"] == 0
    subtitles.run_translation_task(first["task"]["id"])
    assert tasks.get_task(user_id, first["task"]["id"])["state"] == "succeeded"
    result = subtitles.get_translation(user_id, "summary", source_id, "zh")
    assert [(s["start"], s["end"]) for s in result["subtitle"]["segments"]] == [(s["start"], s["end"]) for s in original["segments"]]
    assert result["subtitle"]["full_text"] == "译文0 译文1"
    assert result["subtitle"]["source"] == "translation"
    assert result["saved_at"].endswith("+00:00")
    assert subtitles.submit_translation(user_id, "summary", source_id, "zh", "cached")["result"] == result
    assert model.create.call_count == 1
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api_subtitles import router
    from backend.auth import get_current_user
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as api:
        query = {"source_kind": "summary", "source_id": source_id, "target_language": "zh"}
        assert api.get("/api/subtitles/translation", params=query).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
        assert api.get("/api/subtitles/translation", params=query).json()["data"] == result
        cached_response = api.post("/api/subtitles/translate", json={**query, "request_key": "http-cached"})
        assert cached_response.status_code == 200
        assert cached_response.json()["data"] == {"cached": True, "result": result}
        assert api.post("/api/subtitles/translate", json={**query, "target_language": "fr", "request_key": "invalid"}).status_code == 422
    force = start_translation(user_id, source_id, "force", True)
    model.create.side_effect = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"segments":[]}'))])
    subtitles.run_translation_task(force["task"]["id"])
    assert tasks.get_task(user_id, force["task"]["id"])["state"] == "failed"
    assert subtitles.get_translation(user_id, "summary", source_id, "zh") == result
    assert extension_quota.get_extension_quota(user_id)["translation"] == {"limit": 3, "used": 1, "reserved": 0, "remaining": 2, "unit": "requests"}
    regenerated = start_translation(user_id, source_id, "force-success", True)
    model.create.side_effect = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
        content='{"segments":[{"id":0,"text":"新版一"},{"id":1,"text":"新版二"}]}'))])
    subtitles.run_translation_task(regenerated["task"]["id"])
    replacement = subtitles.get_translation(user_id, "summary", source_id, "zh")
    assert replacement["id"] == result["id"]
    assert replacement["subtitle"]["full_text"] == "新版一 新版二"
    # 计额完成后保存结果出错，整个成功事务必须回滚并保留旧译文。
    from unittest.mock import patch
    rollback = start_translation(user_id, source_id, "save-error", True)
    with patch.object(subtitles, "save_translation", side_effect=RuntimeError("result write failed")):
        with pytest.raises(RuntimeError, match="result write failed"):
            subtitles.run_translation_task(rollback["task"]["id"])
    assert subtitles.get_translation(user_id, "summary", source_id, "zh") == replacement
    assert extension_quota.get_extension_quota(user_id)["translation"]["used"] == 2
    assert extension_quota.get_extension_quota(user_id)["translation"]["reserved"] == 0
    assert tasks.get_task(user_id, rollback["task"]["id"])["state"] != "succeeded"
    tasks.cancel_task(user_id, rollback["task"]["id"])
    subtitles.run_translation_task(rollback["task"]["id"])
    # 打开译文时恰逢重新总结完成，响应也必须读到同一份原文与译文。
    from backend.api_tasks import get_task_result
    real_read = subtitles.get_translation_by_id
    with database.get_db() as db:
        saved_json = db.execute("SELECT result_json FROM summary_results WHERE result_id=?", (source_id,)).fetchone()[0]
    def replace_source_after_translation(*args, **kwargs):
        translation = real_read(*args, **kwargs)
        changed = json.loads(saved_json)
        changed["subtitle"] = {**original, "full_text": "New source", "segments": [{"start": 0, "end": 1, "text": "New source"}]}
        with database.get_db() as writer:
            writer.execute("UPDATE summary_results SET result_json=? WHERE result_id=?", (json.dumps(changed), source_id))
        return translation
    with patch.object(subtitles, "get_translation_by_id", side_effect=replace_source_after_translation):
        restored = get_task_result(first["task"]["id"], SimpleNamespace(id=user_id))["data"]
    with database.get_db() as db:
        db.execute("UPDATE summary_results SET result_json=? WHERE result_id=?", (saved_json, source_id))
    assert restored["source"]["subtitle"] == original
    assert restored["source"]["summary_result"]["subtitle"] == original
    assert restored["result"]["source_hash"] == restored["source"]["source_hash"]
    other = database.create_user("other-translation@example.com", "unused-test-hash")["id"]
    with pytest.raises(HTTPException) as denied:
        subtitles.get_translation(other, "summary", source_id, "zh")
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException):
        subtitles.delete_translation(other, result["id"])
    assert subtitles.delete_translation(user_id, result["id"]) == {"deleted": True}
    assert subtitles.get_translation(user_id, "summary", source_id, "zh") is None
    assert extension_quota.get_extension_quota(user_id)["translation"]["used"] == 2


def test_translation_rejects_incomplete_model_segments_without_retry(model):
    from backend.downloader import VideoError
    install_translation(model)
    segments = [{"start": index, "end": index + 0.5, "text": "segment"} for index in range(105)]
    with VideoSummarizer() as summarizer:
        translated = summarizer.translate_segments(segments, "ja")
        assert len(translated) == 105
        assert translated[104] == {"start": 104, "end": 104.5, "text": "译文104"}
        assert model.create.call_count >= 2
        for content, finish in (
            ('{"segments":[{"id":0,"text":"a"}]}', "stop"),
            ('{"segments":[{"id":0,"text":"a"},{"id":0,"text":"b"}]}', "stop"),
            ('{"segments":[{"id":0,"text":"a"},{"id":1,"text":" "}]}', "stop"),
            ('{"segments":[{"id":0,"text":"a"},{"id":1,"text":"b"}]}', "length"),
            ('not json', "stop"),
        ):
            model.create.reset_mock()
            model.create.side_effect = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])
            with pytest.raises(VideoError):
                summarizer.translate_segments(segments[:2], "zh")
            assert model.create.call_count == 1


def test_translation_cancel_expiry_source_change_and_quota_rollback(translation_account, model, monkeypatch):
    from backend import extension_quota, subtitles, tasks
    user_id, source_id, original = translation_account
    install_translation(model)
    real_create = model.create.side_effect
    for scenario in ("cancel", "expire", "change"):
        response = start_translation(user_id, source_id, scenario)
        task_id = response["task"]["id"]
        def altered_create(**kwargs):
            result = real_create(**kwargs)
            if scenario == "cancel":
                tasks.cancel_task(user_id, task_id)
            elif scenario == "expire":
                with database.get_db() as db:
                    db.execute("UPDATE extension_usage_requests SET expires_at=0 WHERE task_id=?", (task_id,))
            else:
                with database.get_db() as db:
                    changed = {**original, "segments": [{"start": 0, "end": 1, "text": "Changed"}], "full_text": "Changed"}
                    database.save_summary_result(db, user_id, {"url": "https://example.com/video", "language": "zh", "subtitle": changed, "summary": "new", "mindmap": "# new"}, 2)
            return result
        model.create.side_effect = altered_create
        subtitles.run_translation_task(task_id)
        state = tasks.get_task(user_id, task_id)
        assert state["state"] == ("cancelled" if scenario == "cancel" else "failed")
        assert state["error"] is None or state["error"]["code"] == ("SOURCE_CHANGED" if scenario == "change" else "TASK_TIMEOUT")
        assert extension_quota.get_extension_quota(user_id)["translation"]["used"] == 0
        assert extension_quota.get_extension_quota(user_id)["translation"]["reserved"] == 0
        assert subtitles.get_translation(user_id, "summary", source_id, "zh") is None
    reservation = extension_quota.reserve_extension_usage(user_id, "transcription", 3600, "seconds-task")
    with pytest.raises(extension_quota.QuotaError):
        extension_quota.reserve_extension_usage(user_id, "transcription", 1, "exhausted-task")
    extension_quota.release_extension_usage(reservation.request_id)
    # 跨北京时间零点仍在原预占日结算，新一天不继承昨天的用量。
    before_midnight = 1793548799
    monkeypatch.setattr(extension_quota, "time", lambda: before_midnight)
    midnight_reservation = extension_quota.reserve_extension_usage(user_id, "translation", 1, "midnight-task")
    monkeypatch.setattr(extension_quota, "time", lambda: before_midnight + 2)
    with database.get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        extension_quota.commit_extension_usage(midnight_reservation.request_id, db)
        recorded = db.execute("SELECT quota_date,status FROM extension_usage_requests WHERE request_id=?", (midnight_reservation.request_id,)).fetchone()
    assert recorded["quota_date"] == midnight_reservation.quota_date
    assert recorded["status"] == "succeeded"
    assert extension_quota.get_extension_quota(user_id)["translation"]["used"] == 0
    reservation = extension_quota.reserve_extension_usage(user_id, "translation", 1, "rollback-task")
    with pytest.raises(RuntimeError):
        with database.get_db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            extension_quota.commit_extension_usage(reservation.request_id, db)
            raise RuntimeError("result write failed")
    assert extension_quota.get_extension_quota(user_id)["translation"]["used"] == 0
    extension_quota.release_extension_usage(reservation.request_id)
    # 两种来源复用同一字幕结构；完整超长内容在入队前拒绝。
    with database.get_db() as db:
        db.execute("INSERT INTO transcription_results VALUES(?,?,?,?,?,?,?)", (
            "transcribed", user_id, "https://example.com/audio", json.dumps(original), 3, "small", 1))
    assert subtitles.get_subtitle_source(user_id, "transcription", "transcribed")["subtitle"] == original
    calls = model.create.call_count
    for too_long in (
        {**original, "full_text": "x" * 20001, "segments": [{"start": 0, "end": 1, "text": "x" * 20001}]},
        {**original, "full_text": "x", "segments": [{"start": 0, "end": 1, "text": "x"}] * 1001},
    ):
        with database.get_db() as db:
            database.save_summary_result(db, user_id, {"url": "https://example.com/video", "language": "zh", "subtitle": too_long, "summary": "long", "mindmap": "# long"}, 3)
        with pytest.raises(HTTPException) as limit:
            subtitles.submit_translation(user_id, "summary", source_id, "zh", "too-long")
        assert limit.value.status_code == 413
    assert model.create.call_count == calls
