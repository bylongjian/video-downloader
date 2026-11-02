"""队列使用真实 SQLite/HTTP；视频下载仅替换网络边界。"""

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend import database


@pytest.fixture
def task_client(monkeypatch, tmp_path):
    from backend import api_auth, api_tasks, tasks

    monkeypatch.setattr(tasks, "QUEUE_ROOT", tmp_path / "queue")
    monkeypatch.setattr(tasks, "_accepting", True)
    monkeypatch.setattr(tasks, "_stopping", threading.Event())
    database.init_db()
    app = FastAPI()
    app.include_router(api_auth.router)
    app.include_router(api_tasks.router)

    @app.exception_handler(HTTPException)
    async def error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"success": False, **exc.detail})

    with TestClient(app) as client:
        yield client


def register(client, name="queue"):
    data = client.post("/api/auth/register", json={"email": f"{name}@example.com", "password": "password123"}).json()["data"]
    return data["user"]["id"], {"Authorization": "Bearer " + data["token"]}


def test_batch_is_persistent_idempotent_and_private_with_atomic_queue_limits(task_client):
    from backend import tasks

    client = task_client
    user_id, headers = register(client)
    other_id, other = register(client, "other")
    payload = {"request_key": "batch-one", "items": [{"url": " https://example.com/one "}, {"url": "https://example.com/one"}, {"url": "https://example.com/two"}]}
    assert client.post("/api/batches", json=payload).status_code == 401
    created = client.post("/api/batches", json=payload, headers=headers)
    assert created.status_code == 202
    batch = created.json()["data"]
    assert batch["deduplicated_count"] == 1 and len(batch["task_ids"]) == 2
    assert client.post("/api/batches", json=payload, headers=headers).json()["data"] == batch
    assert client.get(f"/api/batches/{batch['id']}", headers=other).status_code == 404
    item_id = batch["task_ids"][0]
    for suffix in ("", "/result", "/file"):
        assert client.get(f"/api/tasks/{item_id}{suffix}", headers=other).status_code == 404
    assert client.post(f"/api/tasks/{item_id}/cancel", headers=other).status_code == 404
    assert client.post(f"/api/tasks/{item_id}/retry", json={"request_key": "steal"}, headers=other).status_code == 404
    page = client.get("/api/tasks?limit=1", headers=headers).json()["data"]
    second = client.get("/api/tasks", params={"limit": 1, "cursor": page["next_cursor"]}, headers=headers).json()["data"]
    assert page["items"][0]["id"] != second["items"][0]["id"] and second["next_cursor"] is None
    assert "file_path" not in page["items"][0] and "task_dir" not in page["items"][0]
    for index in range(28):
        tasks.create_task(user_id, "download", {"url": f"https://example.com/fill-{index}", "format_id": "best"}, f"fill-{index}")
    overflow = client.post("/api/batches", json={"request_key": "overflow", "items": [{"url": "https://example.com/new"}]}, headers=headers)
    assert overflow.status_code == 429
    assert client.post("/api/batches", json=payload, headers=headers).json()["data"] == batch
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM task_batches").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM download_history WHERE origin='task'").fetchone()[0] == 30
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: tasks.create_batch(other_id, "race", [{"url": "https://example.com/race", "format_id": "best"}]), range(2)))
    assert responses[0] == responses[1]
    third_id, _ = register(client, "third")
    fourth_id, fourth_headers = register(client, "fourth")
    for owner, count in ((other_id, 29), (third_id, 30), (fourth_id, 10)):
        for index in range(count):
            tasks.create_task(owner, "download", {"url": f"https://example.com/global-{index}", "format_id": "best"}, f"global-{index}")
    full = client.post("/api/batches", json={"request_key": "global-full", "items": [{"url": "https://example.com/new"}]}, headers=fourth_headers)
    assert full.status_code == 429 and full.json()["code"] == "QUEUE_FULL"


def test_cancel_retry_restart_and_file_expiry_never_commit_cancelled_success(task_client, monkeypatch):
    from backend import tasks

    user_id, headers = register(task_client)
    pending = tasks.create_task(user_id, "download", {"url": "https://example.com/pending", "format_id": "best"}, "pending")
    assert task_client.post(f"/api/tasks/{pending['id']}/cancel", headers=headers).json()["data"]["state"] == "cancelled"
    retry = task_client.post(f"/api/tasks/{pending['id']}/retry", json={"request_key": "retry"}, headers=headers)
    assert retry.status_code == 202
    new = retry.json()["data"]
    assert new["retry_of"] == pending["id"]
    assert task_client.post(f"/api/tasks/{pending['id']}/retry", json={"request_key": "retry"}, headers=headers).json()["data"]["id"] == new["id"]

    def download(self, url, format_id, **kwargs):
        self.download_dir.mkdir(parents=True)
        path = self.download_dir / "video.mp4"
        path.write_bytes(b"test-video")
        if url.endswith("pending"):
            # 模拟取消与下载完成同时发生；下载器已返回文件也不能提交成功。
            tasks.cancel_task(user_id, new["id"])
        return {"filepath": str(path), "task_dir": str(self.download_dir), "filename": "video.mp4", "warning": ""}

    monkeypatch.setattr(tasks.VideoDownloader, "download", download)
    assert tasks.claim_task()["id"] == new["id"]
    tasks.run_download_task(new["id"])
    assert tasks.get_task(user_id, new["id"])["state"] == "cancelled"
    assert not (tasks.QUEUE_ROOT / new["id"]).exists()
    ready = tasks.create_task(user_id, "download", {"url": "https://example.com/ready", "format_id": "best"}, "ready")
    tasks.claim_task()
    tasks.run_download_task(ready["id"])
    assert tasks.get_task(user_id, ready["id"])["file_available"] is True
    assert task_client.get(f"/api/tasks/{ready['id']}/file", headers=headers).content == b"test-video"
    assert task_client.get(f"/api/tasks/{ready['id']}/file", headers=headers).content == b"test-video"
    partial_response = task_client.get(f"/api/tasks/{ready['id']}/file", headers={**headers, "Range": "bytes=0-3"})
    assert partial_response.status_code == 206 and partial_response.content == b"test"
    assert task_client.get(f"/api/tasks/{ready['id']}/result", headers=headers).status_code == 409
    # 领取期间的维护不能删文件；发送完成后再清理。
    lease = tasks.acquire_task_file(user_id, ready["id"])
    with database.get_db() as db:
        db.execute("UPDATE tasks SET file_expires_at=1 WHERE id=?", (ready["id"],))
    tasks.cleanup_task_files()
    assert lease["path"].is_file()
    tasks.release_task_file(ready["id"])
    assert not lease["path"].exists()
    assert task_client.get(f"/api/tasks/{ready['id']}/file", headers=headers).status_code == 410
    interrupted = tasks.create_task(user_id, "download", {"url": "https://example.com/interrupted", "format_id": "best"}, "interrupted")
    queued = tasks.create_task(user_id, "download", {"url": "https://example.com/queued", "format_id": "best"}, "queued")
    with database.get_db() as db:
        db.execute("UPDATE tasks SET created_at=created_at-1 WHERE id=?", (interrupted["id"],))
    tasks.claim_task()
    partial = tasks.QUEUE_ROOT / interrupted["id"]
    partial.mkdir(parents=True)
    (partial / "media.part").write_bytes(b"partial")
    tasks.recover_interrupted_tasks()
    assert tasks.get_task(user_id, interrupted["id"])["state"] == "interrupted"
    assert tasks.get_task(user_id, queued["id"])["state"] == "queued"
    assert not partial.exists()


def test_workers_limit_concurrency_and_isolate_failure(task_client, monkeypatch):
    from backend import tasks
    from backend.downloader import VideoError

    user_id, _ = register(task_client)
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = []
    first_claim = True
    original_claim = tasks.claim_task

    def temporary_lock(kind='download'):
        nonlocal first_claim
        with lock:
            if first_claim:
                first_claim = False
                raise sqlite3.OperationalError("database is locked")
        return original_claim(kind)

    def download(self, url, format_id, **kwargs):
        nonlocal active, maximum
        self.download_dir.mkdir(parents=True)
        path = self.download_dir / "video.mp4"
        path.write_bytes(b"partial")
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls.append(url)
            if len(calls) == 2:
                started.set()
        try:
            assert release.wait(5)
            if url.endswith("bad"):
                raise VideoError("平台暂不可用")
            path.write_bytes(b"video")
            return {"filepath": str(path), "task_dir": str(self.download_dir), "filename": "video.mp4", "warning": ""}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(tasks.VideoDownloader, "download", download)
    monkeypatch.setattr(tasks, "claim_task", temporary_lock)
    items = [tasks.create_task(user_id, "download", {"url": "https://example.com/" + name, "format_id": "best"}, name) for name in ("good", "bad", "third")]

    async def exercise():
        await tasks.start_workers()
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert len(calls) == 2
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                states = [tasks.get_task(user_id, item["id"])["state"] for item in items]
                if all(state in tasks.TERMINAL_STATES for state in states):
                    break
                await asyncio.sleep(0.02)
            assert states.count("succeeded") == 2 and states.count("failed") == 1
        finally:
            release.set()
            await tasks.stop_workers()

    asyncio.run(exercise())
    assert maximum == 2 and len(calls) == 3
    # 停止服务先标记中断，仍阻塞的下载退出前，维护不得删它的目录。
    release.clear()
    started.clear()
    calls.clear()

    async def stopping():
        await tasks.start_workers()
        stopped_items = [tasks.create_task(user_id, "download", {"url": f"https://example.com/stop-{index}", "format_id": "best"}, f"stop-{index}") for index in range(3)]
        stop = None
        try:
            assert await asyncio.to_thread(started.wait, 5)
            stop = asyncio.create_task(tasks.stop_workers())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                states = [tasks.get_task(user_id, item["id"])["state"] for item in stopped_items]
                if states.count("interrupted") == 2:
                    break
                await asyncio.sleep(0.01)
            assert states.count("interrupted") == 2 and states.count("queued") == 1
            await asyncio.to_thread(tasks.cleanup_task_files)
            assert len(list(tasks.QUEUE_ROOT.glob("*/video.mp4"))) == 4  # 两个旧成功文件、两个运行中残留。
            assert not stop.done()
        finally:
            release.set()
            if stop is not None:
                await stop
            else:
                await tasks.stop_workers()
        assert len(list(tasks.QUEUE_ROOT.glob("*/video.mp4"))) == 2

    asyncio.run(stopping())
