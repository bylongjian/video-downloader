import sqlite3

from backend import database, quota


def saved_summary(user_id, url="https://example.com/video"):
    result = {
        "url": url,
        "language": "zh",
        "subtitle": {"has_subtitle": True, "segments": [{"start": 0, "end": 2, "text": "原字幕"}], "full_text": "原字幕"},
        "summary": "已保存摘要",
        "mindmap": "# 已保存导图",
    }
    quota.commit_usage(quota.reserve_usage(user_id, "summary").request_id, summary_result=result)
    return result


def second_account(client):
    data = client.post("/api/auth/register", json={"email": "other-history@example.com", "password": "password123"}).json()["data"]
    return {"Authorization": "Bearer " + data["token"]}


def test_history_restores_saved_summary_and_pages_without_model_or_video(client, auth_headers, monkeypatch):
    user_id = client.get("/api/auth/me", headers=auth_headers).json()["data"]["user"]["id"]
    original = saved_summary(user_id)
    # 同一秒保存多条记录，翻页仍须使用 ID 作第二排序字段。
    with database.get_db() as db:
        for index in range(21):
            database.save_summary_result(db, user_id, {**original, "url": f"https://example.com/older-{index}"}, 1)
    before = quota.get_quota(user_id)
    from backend import main, summarizer
    def forbidden(*args, **kwargs):
        raise AssertionError("历史读取不能访问视频或模型")
    monkeypatch.setattr(main.downloader, "extract_info", forbidden)
    monkeypatch.setattr(summarizer.SubtitleExtractor, "extract", forbidden)
    monkeypatch.setattr(summarizer.VideoSummarizer, "__init__", forbidden)
    page = client.get("/api/history?kind=summary", headers=auth_headers)
    assert page.status_code == 200
    data = page.json()["data"]
    assert len(data["items"]) == 20
    assert data["next_cursor"]
    item = data["items"][0]
    assert item["url"] == original["url"]
    assert item["video_title"] == "未记录标题"
    assert item["status"] == "succeeded"
    assert item["updated_at"].endswith("+00:00")
    detail = client.get(f"/api/history/summary/{item['id']}", headers=auth_headers).json()["data"]
    assert detail["result"]["summary"] == original["summary"]
    assert detail["result"]["subtitle"] == original["subtitle"]
    assert detail["result"]["result_id"] == item["id"]
    assert detail["result"]["saved_at"].endswith("+00:00")
    second = client.get("/api/history", params={"kind": "summary", "cursor": data["next_cursor"]}, headers=auth_headers).json()["data"]
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None
    assert not {row["id"] for row in data["items"]} & {row["id"] for row in second["items"]}
    assert quota.get_quota(user_id)["summary"] == before["summary"]


def test_download_history_enforces_owner_and_browser_status(client, auth_headers):
    other = second_account(client)
    assert client.get("/api/history?kind=download").status_code == 401
    created = client.post("/api/history/downloads", json={"url": "https://example.com/video", "video_title": "测试标题", "platform": "example"}, headers=auth_headers)
    assert created.status_code == 201
    item_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "started"
    for method, path, kwargs in (
        ("get", f"/api/history/download/{item_id}", {}),
        ("delete", f"/api/history/download/{item_id}", {}),
        ("post", f"/api/history/downloads/{item_id}/events", {"json": {"event": "failed"}}),
    ):
        response = getattr(client, method)(path, headers=other, **kwargs)
        assert response.status_code == 404
        assert response.json()["code"] == "HISTORY_NOT_FOUND"
    assert client.get("/api/history?kind=download", headers=other).json()["data"]["items"] == []
    event_path = f"/api/history/downloads/{item_id}/events"
    assert client.post(event_path, json={"event": "succeeded"}, headers=auth_headers).status_code == 422
    event = client.post(event_path, json={"event": "browser_handoff"}, headers=auth_headers)
    assert event.json()["data"] == {"id": item_id, "status": "browser_handoff", "reported_by": "browser"}
    assert client.post(event_path, json={"event": "browser_handoff"}, headers=auth_headers).status_code == 200
    assert client.post(event_path, json={"event": "failed"}, headers=auth_headers).status_code == 409
    with database.get_db() as db:
        db.execute("UPDATE download_history SET origin='task',task_id='server-task',status='ready' WHERE id=?", (item_id,))
    assert client.post(event_path, json={"event": "failed"}, headers=auth_headers).status_code == 409
    detail = client.get(f"/api/history/download/{item_id}", headers=auth_headers).json()["data"]
    assert detail["status"] == "ready"
    assert detail["task_id"] == "server-task"
    assert "filepath" not in detail and "direct_url" not in detail
    client.post("/api/auth/logout", headers=auth_headers)
    assert client.get("/api/history?kind=download", headers=auth_headers).status_code == 401


def test_deleting_history_removes_restore_but_keeps_spent_quota_and_database_failures(client, auth_headers, monkeypatch):
    user_id = client.get("/api/auth/me", headers=auth_headers).json()["data"]["user"]["id"]
    original = saved_summary(user_id)
    response = client.get("/api/history?kind=summary", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    path = f"/api/history/summary/{item['id']}"
    other = second_account(client)
    assert client.get(path, headers=other).status_code == 404
    assert client.delete(path, headers=other).status_code == 404
    assert client.delete(path, headers=auth_headers).json()["data"] == {"deleted": True}
    assert client.get(path, headers=auth_headers).status_code == 404
    assert client.delete(path, headers=auth_headers).status_code == 404
    assert client.get("/api/summarize/result", params={"url": original["url"]}, headers=auth_headers).json()["data"] is None
    assert quota.get_quota(user_id)["summary"]["used"] == 1
    download = client.post("/api/history/downloads", json={"url": original["url"]}, headers=auth_headers).json()["data"]
    assert client.delete(f"/api/history/download/{download['id']}", headers=auth_headers).status_code == 200
    assert client.get("/api/history?kind=download", headers=auth_headers).json()["data"]["items"] == []
    assert client.get("/api/history", params={"kind": "summary", "cursor": "invalid"}, headers=auth_headers).status_code == 422
    from backend import history
    def unavailable():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(history, "get_db", unavailable)
    outage = client.get("/api/history?kind=summary", headers=auth_headers)
    assert outage.status_code == 503
    assert outage.json()["success"] is False
