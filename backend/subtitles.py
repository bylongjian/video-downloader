"""本人已保存字幕是唯一翻译来源；完整结果、计额和任务成功一起写入。"""

import hashlib
import json
import secrets
from contextlib import nullcontext
from datetime import datetime, timezone
from time import time
from typing import Literal

from fastapi import HTTPException

from . import extension_quota, tasks
from .database import get_db
from .downloader import VideoError
from .quota import QuotaError
from .summarizer import VideoSummarizer, subtitle_result

SourceKind = Literal["summary", "transcription"]
TargetLanguage = Literal["zh", "en", "ja"]


def get_subtitle_source(user_id: int, source_kind: SourceKind, source_id: str, *, db=None) -> dict:
    with nullcontext(db) if db is not None else get_db() as connection:
        if source_kind == "summary":
            row = connection.execute("SELECT url,result_json FROM summary_results WHERE user_id=? AND result_id=?", (user_id, source_id)).fetchone()
        elif source_kind == "transcription":
            row = connection.execute("SELECT url,result_json FROM transcription_results WHERE user_id=? AND id=?", (user_id, source_id)).fetchone()
        else:
            raise HTTPException(422, {"code": "INVALID_SOURCE", "error": "请选择已保存的总结或转写字幕"})
        if row is None:
            raise HTTPException(404, {"code": "SOURCE_NOT_FOUND", "error": "字幕来源不存在或无权访问"})
        content = json.loads(row["result_json"])
        subtitle = content["subtitle"] if source_kind == "summary" else content
        source_hash = hashlib.sha256(json.dumps(subtitle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"source_kind": source_kind, "source_id": source_id, "source_hash": source_hash,
                "url": row["url"], "subtitle": subtitle}


def _translation_result(row) -> dict:
    return {"id": row["id"], "source_kind": row["source_kind"], "source_id": row["source_id"],
            "source_hash": row["source_hash"], "target_language": row["target_language"],
            "subtitle": json.loads(row["result_json"]),
            "saved_at": datetime.fromtimestamp(row["updated_at"], timezone.utc).isoformat()}


def _find_translation(db, user_id: int, source: dict, target_language: TargetLanguage) -> dict | None:
    row = db.execute("""SELECT * FROM subtitle_translations WHERE user_id=? AND source_kind=?
        AND source_id=? AND source_hash=? AND target_language=?""",
        (user_id, source["source_kind"], source["source_id"], source["source_hash"], target_language)).fetchone()
    return _translation_result(row) if row else None


def get_translation(user_id: int, source_kind: SourceKind, source_id: str, target_language: TargetLanguage) -> dict | None:
    with get_db() as db:
        source = get_subtitle_source(user_id, source_kind, source_id, db=db)
        return _find_translation(db, user_id, source, target_language)


def get_translation_by_id(user_id: int, translation_id: str, *, db=None) -> dict:
    with nullcontext(db) if db is not None else get_db() as connection:
        row = connection.execute("SELECT * FROM subtitle_translations WHERE user_id=? AND id=?", (user_id, translation_id)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "TRANSLATION_NOT_FOUND", "error": "译文不存在或无权访问"})
        source = get_subtitle_source(user_id, row["source_kind"], row["source_id"], db=connection)
        if row["source_hash"] != source["source_hash"]:
            raise HTTPException(409, {"code": "SOURCE_CHANGED", "error": "字幕来源已变化，请重新选择当前字幕"})
        return _translation_result(row)


def save_translation(db, user_id: int, source: dict, target_language: TargetLanguage, subtitle: dict) -> dict:
    now = int(time())
    db.execute("""INSERT INTO subtitle_translations(id,user_id,source_kind,source_id,source_hash,target_language,result_json,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,source_kind,source_id,source_hash,target_language)
        DO UPDATE SET result_json=excluded.result_json,updated_at=excluded.updated_at""",
        (secrets.token_urlsafe(24), user_id, source["source_kind"], source["source_id"], source["source_hash"], target_language,
         json.dumps(subtitle, ensure_ascii=False), now))
    return _find_translation(db, user_id, source, target_language)


def delete_translation(user_id: int, translation_id: str) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM subtitle_translations WHERE user_id=? AND id=?", (user_id, translation_id)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "TRANSLATION_NOT_FOUND", "error": "译文不存在或无权访问"})
        busy = db.execute("""SELECT 1 FROM tasks WHERE user_id=? AND kind='translation'
            AND state IN ('queued','running','cancelling')
            AND json_extract(input_json,'$.source_kind')=? AND json_extract(input_json,'$.source_id')=?""",
            (user_id, row["source_kind"], row["source_id"])).fetchone()
        if busy:
            raise HTTPException(409, {"code": "SOURCE_BUSY", "error": "该字幕仍有翻译任务，请先取消任务再删除译文"})
        db.execute("DELETE FROM subtitle_translations WHERE user_id=? AND id=?", (user_id, translation_id))
    return {"deleted": True}


def _validate_translation_source(source: dict):
    subtitle = source["subtitle"]
    if not subtitle["has_subtitle"] or not subtitle["segments"]:
        raise HTTPException(409, {"code": "NO_SUBTITLE", "error": "该结果没有可翻译字幕，请先保存成功字幕"})
    if len(subtitle["segments"]) > 1000 or max(len(subtitle["full_text"]), sum(len(segment["text"]) for segment in subtitle["segments"])) > 20000:
        raise HTTPException(413, {"code": "SUBTITLE_TOO_LONG", "error": "翻译支持完整原文最多 20000 字符、1000 段，不会截断字幕"})


def submit_translation(user_id: int, source_kind: SourceKind, source_id: str, target_language: TargetLanguage,
                       request_key: str, force: bool = False) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id,kind FROM tasks WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone()
        if existing:
            if existing["kind"] != "translation":
                raise HTTPException(409, {"code": "REQUEST_KEY_CONFLICT", "error": "该请求标识已用于其他任务，请刷新后重试"})
            return {"cached": False, "task": tasks.get_task(user_id, existing["id"])}
        source = get_subtitle_source(user_id, source_kind, source_id, db=db)
        _validate_translation_source(source)
        source_key = json.dumps([source_kind, source_id, source["source_hash"], target_language], separators=(",", ":"))
        active = db.execute("""SELECT id FROM tasks WHERE user_id=? AND kind='translation' AND source_key=?
            AND state IN ('queued','running','cancelling')""", (user_id, source_key)).fetchone()
        if active:
            return {"cached": False, "task": tasks.get_task(user_id, active["id"])}
        cached = _find_translation(db, user_id, source, target_language)
        if cached is not None and not force:
            return {"cached": True, "result": cached}
        task = tasks.create_task(user_id, "translation", {
            "source_kind": source_kind, "source_id": source_id, "source_hash": source["source_hash"],
            "target_language": target_language, "force": force, "url": source["url"],
        }, request_key, source_key=source_key, db=db)
        return {"cached": False, "task": task}


def _finish_translation_failure(task_id: str, code: str, message: str):
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        now = int(time())
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE task_id=? AND status='reserved'", (now, task_id))
        row = db.execute("SELECT state,cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["state"] in tasks.TERMINAL_STATES:
            return
        cancelled = bool(row["cancel_requested"])
        db.execute("UPDATE tasks SET state=?,stage=?,error_code=?,error_message=?,finished_at=?,version=version+1 WHERE id=?",
                   ("cancelled" if cancelled else "failed", "cancelled" if cancelled else "failed",
                    None if cancelled else code, None if cancelled else message, now, task_id))


def run_translation_task(task_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=? AND kind='translation'", (task_id,)).fetchone()
    if row is None or row["state"] not in {"running", "cancelling"}:
        return
    data = json.loads(row["input_json"])
    reservation = None
    try:
        tasks.ensure_task_active(task_id)
        source = get_subtitle_source(row["user_id"], data["source_kind"], data["source_id"])
        if source["source_hash"] != data["source_hash"]:
            raise HTTPException(409, {"code": "SOURCE_CHANGED", "error": "字幕来源已变化，本次没有保存或计次，请重新提交"})
        _validate_translation_source(source)
        reservation = extension_quota.reserve_extension_usage(row["user_id"], "translation", 1, task_id)

        def check_active():
            tasks.ensure_task_active(task_id)
            if time() >= reservation.expires_at:
                raise tasks.TaskStopped("翻译超过处理期限")

        with get_db() as db:
            db.execute("UPDATE tasks SET stage='translating',version=version+1 WHERE id=? AND state='running'", (task_id,))
        check_active()
        with VideoSummarizer() as summarizer:
            segments = summarizer.translate_segments(source["subtitle"]["segments"], data["target_language"], check_active=check_active)
        subtitle = subtitle_result(segments, data["target_language"], "translated", "translation")
        with get_db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state,cancel_requested,started_at FROM tasks WHERE id=?", (task_id,)).fetchone()
            if current is None or current["state"] != "running" or current["cancel_requested"] or (current["started_at"] is not None and time() >= current["started_at"] + 900):
                raise tasks.TaskStopped("任务已取消、中断或超过执行期限")
            fresh_source = get_subtitle_source(row["user_id"], data["source_kind"], data["source_id"], db=db)
            if fresh_source["source_hash"] != source["source_hash"]:
                raise HTTPException(409, {"code": "SOURCE_CHANGED", "error": "字幕来源已变化，本次没有保存或计次，请重新提交"})
            extension_quota.commit_extension_usage(reservation.request_id, db)
            result = save_translation(db, row["user_id"], source, data["target_language"], subtitle)
            db.execute("UPDATE tasks SET state='succeeded',stage='ready',result_id=?,finished_at=?,version=version+1 WHERE id=?",
                       (result["id"], int(time()), task_id))
    except tasks.TaskStopped as exc:
        _finish_translation_failure(task_id, "TASK_TIMEOUT", str(exc))
    except VideoError as exc:
        _finish_translation_failure(task_id, "TRANSLATION_FAILED", str(exc))
    except QuotaError as exc:
        _finish_translation_failure(task_id, "TASK_TIMEOUT" if exc.code == "QUOTA_COMMIT_FAILED" else exc.code, str(exc))
    except HTTPException as exc:
        code = "SOURCE_CHANGED" if exc.status_code == 404 else exc.detail["code"]
        _finish_translation_failure(task_id, code, exc.detail["error"])
    finally:
        if reservation:
            extension_quota.release_extension_usage(reservation.request_id)
