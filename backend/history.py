"""个人历史直接读取成功总结和下载元信息，不触发视频或模型请求。"""

import base64
import binascii
import json
import secrets
from contextlib import nullcontext
from datetime import datetime, timezone
from time import time
from typing import Literal

from fastapi import HTTPException

from .database import get_db

HistoryKind = Literal["download", "summary"]
DownloadEvent = Literal["browser_handoff", "failed", "cancelled"]


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _cursor_position(cursor: str, kind: HistoryKind) -> tuple[int, str]:
    # 游标来自客户端，是需要检查的输入边界。
    try:
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError("invalid cursor")
        cursor_kind, timestamp, item_id = value
        if cursor_kind != kind or type(timestamp) is not int or not -(2**63) <= timestamp < 2**63 or not isinstance(item_id, str) or not item_id:
            raise ValueError("invalid cursor")
        return timestamp, item_id
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(422, {"code": "INVALID_CURSOR", "error": "历史分页位置无效，请刷新列表后重试"}) from exc


def _item(row, kind: HistoryKind) -> dict:
    item = {
        "id": row["result_id"] if kind == "summary" else row["id"],
        "kind": kind,
        "url": row["url"],
        "video_title": row["video_title"] or "未记录标题",
        "platform": row["platform"],
        "status": "succeeded" if kind == "summary" else row["status"],
        "updated_at": _iso(row["updated_at"]),
        "task_id": None if kind == "summary" else row["task_id"],
    }
    if kind == "summary":
        item["language"] = row["language"]
    else:
        item.update(format_id=row["format_id"], created_at=_iso(row["created_at"]))
    return item


def list_history(user_id: int, kind: HistoryKind, limit: int = 20, cursor: str | None = None) -> dict:
    table, id_column = ("summary_results", "result_id") if kind == "summary" else ("download_history", "id")
    columns = "result_id,url,language,video_title,platform,updated_at" if kind == "summary" else "id,url,video_title,platform,format_id,status,created_at,updated_at,task_id"
    parameters = [user_id]
    after = ""
    if cursor is not None:
        updated_at, item_id = _cursor_position(cursor, kind)
        after = f" AND (updated_at < ? OR (updated_at = ? AND {id_column} < ?))"
        parameters.extend((updated_at, updated_at, item_id))
    with get_db() as db:
        rows = db.execute(f"SELECT {columns} FROM {table} WHERE user_id=?{after} ORDER BY updated_at DESC,{id_column} DESC LIMIT ?", (*parameters, limit + 1)).fetchall()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        value = json.dumps([kind, last["updated_at"], last[id_column]], separators=(",", ":")).encode()
        next_cursor = base64.urlsafe_b64encode(value).decode().rstrip("=")
    return {"items": [_item(row, kind) for row in rows[:limit]], "next_cursor": next_cursor}


def get_history_detail(user_id: int, kind: HistoryKind, item_id: str, *, db=None) -> dict:
    table, id_column = ("summary_results", "result_id") if kind == "summary" else ("download_history", "id")
    with nullcontext(db) if db is not None else get_db() as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE user_id=? AND {id_column}=?", (user_id, item_id)).fetchone()
    if row is None:
        raise HTTPException(404, {"code": "HISTORY_NOT_FOUND", "error": "历史记录不存在或无权访问"})
    item = _item(row, kind)
    if kind == "summary":
        item["result"] = {**json.loads(row["result_json"]), "saved_at": _iso(row["updated_at"]), "result_id": row["result_id"]}
    else:
        item.update(origin=row["origin"], filename=row["filename"], error_code=row["error_code"])
    return item


def delete_history(user_id: int, kind: HistoryKind, item_id: str) -> dict:
    table, id_column = ("summary_results", "result_id") if kind == "summary" else ("download_history", "id")
    task_to_clean = None
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(f"SELECT * FROM {table} WHERE user_id=? AND {id_column}=?", (user_id, item_id)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "HISTORY_NOT_FOUND", "error": "历史记录不存在或无权访问"})
        if kind == 'summary':
            busy = db.execute("SELECT 1 FROM tasks WHERE user_id=? AND kind='translation' AND state IN ('queued','running','cancelling') AND json_extract(input_json,'$.source_kind')='summary' AND json_extract(input_json,'$.source_id')=?",(user_id,item_id)).fetchone()
            if busy:
                raise HTTPException(409,{'code':'SOURCE_BUSY','error':'该总结仍有翻译任务，请先取消任务再删除'})
            db.execute("DELETE FROM subtitle_translations WHERE user_id=? AND source_kind='summary' AND source_id=?",(user_id,item_id))
        elif row['origin'] == 'task':
            task = db.execute('SELECT state FROM tasks WHERE user_id=? AND id=?',(user_id,row['task_id'])).fetchone()
            if task and task['state'] in {'queued','running','cancelling'}:
                raise HTTPException(409,{'code':'SOURCE_BUSY','error':'下载仍在处理中，请先取消任务后再删除历史'})
            task_to_clean = row['task_id']
        db.execute(f"DELETE FROM {table} WHERE user_id=? AND {id_column}=?", (user_id, item_id))
    if task_to_clean:
        from .tasks import remove_task_file
        remove_task_file(user_id,task_to_clean)
    return {"deleted": True}


def create_download_history(user_id: int, *, url: str, video_title: str = "", platform: str = "", format_id: str = "bestvideo+bestaudio/best") -> dict:
    item_id = secrets.token_urlsafe(24)
    now = int(time())
    with get_db() as db:
        db.execute("""INSERT INTO download_history
            (id,user_id,url,video_title,platform,format_id,status,origin,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'started','browser',?,?)""", (item_id, user_id, url, video_title, platform, format_id, now, now))
    return {"id": item_id, "status": "started"}


def record_download_event(user_id: int, item_id: str, event: DownloadEvent) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT origin,status FROM download_history WHERE user_id=? AND id=?", (user_id, item_id)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "HISTORY_NOT_FOUND", "error": "历史记录不存在或无权访问"})
        if row["origin"] != "browser":
            raise HTTPException(409, {"code": "HISTORY_EVENT_CONFLICT", "error": "后台任务状态由服务器更新，不能由浏览器覆盖"})
        if row["status"] not in {"started", event}:
            raise HTTPException(409, {"code": "HISTORY_EVENT_CONFLICT", "error": "该下载已结束，请刷新历史后查看"})
        if row["status"] == "started":
            db.execute("UPDATE download_history SET status=?,updated_at=? WHERE user_id=? AND id=?", (event, int(time()), user_id, item_id))
    return {"id": item_id, "status": event, "reported_by": "browser"}
