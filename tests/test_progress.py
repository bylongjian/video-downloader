"""WebSocket 用真实 ASGI 和 SQLite；只替换视频平台网络边界。"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import database


ORIGIN = {"origin": "http://localhost:5173"}


@pytest.fixture
def progress_client(monkeypatch, tmp_path):
    from backend import api_progress, tasks

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setattr(tasks, "QUEUE_ROOT", tmp_path / "queue")
    monkeypatch.setattr(tasks, "_accepting", True)
    monkeypatch.setattr(tasks, "_stopping", threading.Event())
    database.init_db()
    app = FastAPI()
    app.include_router(api_progress.router)
    with TestClient(app) as client:
        yield client


def account(name="owner"):
    from backend import auth

    user = database.create_user(f"{name}@example.com", "test-password-hash")
    session = database.create_session(user["id"], int(time.time()) + 3600)
    return user["id"], auth.create_token(user["id"], session), session


def new_task(user_id, key="download"):
    from backend import tasks

    return tasks.create_task(user_id, "download", {"url": "https://example.com/video", "format_id": "best"}, key)


def authenticate(ws, token):
    ws.send_json({"type": "authenticate", "token": token})


def test_socket_streams_real_download_hook_and_persisted_terminal_snapshot(progress_client, monkeypatch):
    from backend import tasks

    user_id, token, _ = account()
    item = new_task(user_id)
    tasks.claim_task()
    release = threading.Event()
    emitted = threading.Event()

    def extract(self, url, options, download=False):
        if not download:
            return {'protocol':'https','url':url}
        path = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
        path.write_bytes(b"video")
        options["progress_hooks"][0]({
            "status": "downloading", "filename": str(path), "downloaded_bytes": 25,
            "total_bytes": 100, "speed": 10, "eta": 7,
            "info_dict": {"format_id": "137", "url": "https://cdn.invalid/private"},
        })
        emitted.set()
        assert release.wait(5)
        options['progress_hooks'][0]({'status':'finished','filename':str(path),'downloaded_bytes':100,'total_bytes':100,'info_dict':{'format_id':'137'}})
        options['postprocessor_hooks'][0]({'status':'started','postprocessor':'Merger'})
        merging = tasks.get_task(user_id, item['id'])
        assert merging['stage'] == 'merging' and merging['progress']['percent'] is None
        return {"filepath": str(path), "title": "video"}

    monkeypatch.setattr(tasks.VideoDownloader, "_extract", extract)
    worker = threading.Thread(target=tasks.run_download_task, args=(item["id"],))
    try:
        with progress_client.websocket_connect(f"/api/tasks/{item['id']}/progress", headers=ORIGIN) as ws:
            authenticate(ws, token)
            initial = ws.receive_json()
            assert initial == {"type": "snapshot", "task": tasks.get_task(user_id, item["id"])}
            worker.start()
            assert emitted.wait(5)
            updated = ws.receive_json()
            assert updated["type"] == "update"
            snapshot = updated["task"]
            assert snapshot["version"] > initial["task"]["version"]
            assert snapshot["progress"] == {
                "downloaded_bytes": 25, "total_bytes": 100, "total_is_estimate": False,
                "speed_bytes_per_second": 10, "eta_seconds": 7, "track_id": "137", "percent": 25.0,
            }
            assert "private" not in json.dumps(updated) and "file_path" not in snapshot
            release.set()
            worker.join(5)
            assert not worker.is_alive()
            terminal = ws.receive_json()
            assert terminal["task"]["state"] == "succeeded"
            assert terminal["task"] == tasks.get_task(user_id, item["id"])
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1000
        with progress_client.websocket_connect(f"/api/tasks/{item['id']}/progress", headers=ORIGIN) as ws:
            authenticate(ws, token)
            assert ws.receive_json() == {"type": "snapshot", "task": terminal["task"]}
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1000
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)


def test_socket_rejects_bad_origin_auth_other_user_and_revoked_session(progress_client, monkeypatch):
    from backend import api_progress

    user_id, token, session_id = account()
    _, other_token, _ = account("other")
    item = new_task(user_id)
    path = f"/api/tasks/{item['id']}/progress"
    monkeypatch.setattr(api_progress, "AUTH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(api_progress, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(api_progress, "POLL_SECONDS", 0.02)
    for headers, url in (({}, path), ({"origin": "https://evil.invalid"}, path), (ORIGIN, path + "?token=" + token)):
        with pytest.raises(WebSocketDisconnect) as rejected:
            with progress_client.websocket_connect(url, headers=headers):
                pass
        assert rejected.value.code == 1008
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(WebSocketDisconnect) as rejected:
        with progress_client.websocket_connect(path, headers=ORIGIN):
            pass
    assert rejected.value.code == 1008
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    for frame in (None, [], {"type": "authenticate", "token": "invalid"}, {"type": "authenticate", "token": other_token}, {"type": "authenticate", "token": 42}):
        with progress_client.websocket_connect(path, headers=ORIGIN) as ws:
            if frame is not None:
                ws.send_json(frame)
            with pytest.raises(WebSocketDisconnect) as rejected:
                ws.receive_json()
            assert rejected.value.code == 1008
    with progress_client.websocket_connect(path, headers=ORIGIN) as ws:
        authenticate(ws, token)
        assert ws.receive_json()["type"] == "snapshot"
        assert ws.receive_json()["type"] == "heartbeat"
        database.revoke_session(session_id)
        with pytest.raises(WebSocketDisconnect) as rejected:
            while True:
                assert ws.receive_json()["type"] == "heartbeat"
        assert rejected.value.code == 1008
    with progress_client.websocket_connect(path, headers=ORIGIN) as ws:
        authenticate(ws, token)
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 1008


def test_reconnect_reads_latest_sqlite_snapshot_without_inventing_unknown_progress(progress_client):
    from backend import tasks

    user_id, token, _ = account()
    item = new_task(user_id)
    path = f"/api/tasks/{item['id']}/progress"
    with progress_client.websocket_connect(path, headers=ORIGIN) as ws:
        authenticate(ws, token)
        before = ws.receive_json()["task"]
    unknown = {
        "downloaded_bytes": 4096, "total_bytes": None, "total_is_estimate": False,
        "speed_bytes_per_second": None, "eta_seconds": None, "track_id": "audio", "percent": None,
    }
    with database.get_db() as db:
        db.execute("UPDATE tasks SET state='running',stage='downloading',version=version+1,progress_json=? WHERE id=?", (json.dumps(unknown), item["id"]))
    with progress_client.websocket_connect(path, headers=ORIGIN) as ws:
        authenticate(ws, token)
        reconnected = ws.receive_json()
        assert reconnected["type"] == "snapshot"
        assert reconnected["task"] == tasks.get_task(user_id, item["id"])
        assert reconnected["task"]["version"] > before["version"]
        assert reconnected["task"]["progress"] == unknown
    tasks.cancel_task(user_id, item["id"])
