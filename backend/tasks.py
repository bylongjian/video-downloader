"""SQLite 保存任务；两个工作线程下载，网络调用期间不持有数据库事务。"""

import asyncio
import base64
import binascii
import json
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from .database import get_db
from .downloader import DownloadCancelled, DownloadLimitExceeded, VideoDownloader, VideoError, validate_public_url

TaskKind = Literal["download", "translation", "transcription"]
TaskState = Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted"]
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "interrupted")
ALL_STATES = ("queued", "running", "cancelling", *TERMINAL_STATES)
QUEUE_ROOT = Path(os.getenv("DOWNLOAD_DIR", str(Path(__file__).parent / "downloads"))) / "queue"
MAX_FILE_BYTES = 2 * 1024**3
MAX_QUEUE_BYTES = 10 * 1024**3
DOWNLOAD_BUDGET_SECONDS = 30 * 60
FILE_RETENTION_SECONDS = 24 * 3600
SNAPSHOT_RETENTION_SECONDS = 30 * 24 * 3600
logger = logging.getLogger(__name__)

_workers: list[asyncio.Task] = []
_maintenance: asyncio.Task | None = None
_stopping = threading.Event()
_accepting = True
_file_lock = threading.RLock()
_file_leases: dict[str, int] = {}
_pending_cleanup: set[str] = set()
DOWNLOAD_SLOTS = threading.BoundedSemaphore(2)


class TaskStopped(Exception):
    """任务取消、停止或超过执行期限，禁止再提交成功。"""


def ensure_task_active(task_id: str):
    with get_db() as db:
        row = db.execute('SELECT state,cancel_requested,started_at,kind FROM tasks WHERE id=?', (task_id,)).fetchone()
    if _stopping.is_set() or row is None or row['state'] != 'running' or row['cancel_requested']:
        raise TaskStopped('任务已取消或中断')
    budget = 900 if row['kind'] == 'translation' else 1800
    if row['started_at'] is not None and time.time() >= row['started_at'] + budget:
        raise TaskStopped('任务超过执行期限')
_executing: set[str] = set()


def _error(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "error": message})


def _iso(timestamp: int | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _row(db, user_id: int, task_id: str):
    row = db.execute("SELECT * FROM tasks WHERE user_id=? AND id=?", (user_id, task_id)).fetchone()
    if row is None:
        _error(404, "TASK_NOT_FOUND", "任务不存在或无权访问")
    return row


def _snapshot(row) -> dict:
    data = json.loads(row["input_json"])
    return {
        "id": row["id"], "kind": row["kind"], "batch_id": row["batch_id"], "retry_of": row["retry_of"],
        "state": row["state"], "stage": row["stage"], "version": row["version"],
        "url": data.get("url"), "format_id": data.get("format_id"),
        "progress": json.loads(row["progress_json"]), "result_id": row["result_id"],
        "error": {"code": row["error_code"], "message": row["error_message"]} if row["error_code"] else None,
        "file_available": bool(row["state"] == "succeeded" and row["file_path"] and row["file_expires_at"] and row["file_expires_at"] > time.time() and (QUEUE_ROOT / row["file_path"]).is_file()),
        "file_expires_at": _iso(row["file_expires_at"]), "filename": row["filename"], "warning": row["warning"],
        "created_at": _iso(row["created_at"]), "started_at": _iso(row["started_at"]), "finished_at": _iso(row["finished_at"]),
    }


def get_task(user_id: int, task_id: str) -> dict:
    with get_db() as db:
        return _snapshot(_row(db, user_id, task_id))


def _check_capacity(db, user_id: int, incoming: int):
    if not _accepting:
        _error(503, "QUEUE_STOPPING", "服务正在停止，请稍后重新提交")
    counts = db.execute("""SELECT COUNT(*),COALESCE(SUM(user_id=?),0) FROM tasks
        WHERE state IN ('queued','running','cancelling')""", (user_id,)).fetchone()
    if counts[1] + incoming > 30:
        _error(429, "USER_QUEUE_FULL", "您最多可保留 30 个未完成任务，请等待或取消部分任务")
    if counts[0] + incoming > 100:
        _error(429, "QUEUE_FULL", "当前队列已满，请稍后再试")


def _insert_task(db, user_id: int, kind: TaskKind, input_data: dict, request_key: str, *, batch_id: str | None = None, retry_of: str | None = None, source_key: str | None = None) -> str:
    task_id = secrets.token_urlsafe(24)
    now = int(time.time())
    db.execute("""INSERT INTO tasks(id,user_id,kind,batch_id,request_key,retry_of,input_json,state,stage,
        version,progress_json,cancel_requested,created_at,source_key) VALUES(?,?,?,?,?,?,?,'queued','queued',1,'{}',0,?,?)""",
        (task_id, user_id, kind, batch_id, request_key, retry_of, json.dumps(input_data, ensure_ascii=False), now, source_key))
    if kind == "download":
        db.execute("""INSERT INTO download_history(id,user_id,url,video_title,platform,format_id,status,origin,task_id,created_at,updated_at)
            VALUES(?,?,?,'','',?,'queued','task',?,?,?)""", (secrets.token_urlsafe(24), user_id, input_data["url"], input_data["format_id"], task_id, now, now))
    return task_id


def create_task(user_id: int, kind: TaskKind, input_data: dict, request_key: str, *, batch_id: str | None = None, retry_of: str | None = None, source_key: str | None = None, db=None) -> dict:
    """执行模块先校验业务输入，再调用此函数持久入队；幂等键优先于容量检查。"""
    if db is None:
        with get_db() as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            return create_task(user_id,kind,input_data,request_key,batch_id=batch_id,retry_of=retry_of,source_key=source_key,db=connection)
    existing = db.execute("SELECT * FROM tasks WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone()
    if existing:
        if existing['kind'] != kind:
            _error(409,'REQUEST_KEY_CONFLICT','这个请求标识已用于另一种任务，请重新提交')
        return _snapshot(existing)
    if source_key is not None:
        active = db.execute("SELECT * FROM tasks WHERE user_id=? AND kind=? AND source_key=? AND state IN ('queued','running','cancelling')", (user_id,kind,source_key)).fetchone()
        if active:
            return _snapshot(active)
    _check_capacity(db, user_id, 1)
    task_id = _insert_task(db, user_id, kind, input_data, request_key, batch_id=batch_id, retry_of=retry_of, source_key=source_key)
    return _snapshot(_row(db, user_id, task_id))


def _batch_response(db, row) -> dict:
    ids = [item[0] for item in db.execute("SELECT id FROM tasks WHERE batch_id=? ORDER BY created_at,id", (row["id"],))]
    return {"id": row["id"], "task_ids": ids, "deduplicated_count": row["deduplicated_count"]}


def create_batch(user_id: int, request_key: str, items: list[dict]) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM task_batches WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone()
        if existing:
            return _batch_response(db, existing)
        unique = list({(item["url"].strip(), item["format_id"]): item for item in items})
        _check_capacity(db, user_id, len(unique))
        batch_id = secrets.token_urlsafe(24)
        db.execute("INSERT INTO task_batches(id,user_id,request_key,created_at,deduplicated_count) VALUES(?,?,?,?,?)",
                   (batch_id, user_id, request_key, int(time.time()), len(items) - len(unique)))
        for index, (url, format_id) in enumerate(unique):
            _insert_task(db, user_id, "download", {"url": url, "format_id": format_id}, f"batch:{batch_id}:{index}", batch_id=batch_id)
        return _batch_response(db, db.execute("SELECT * FROM task_batches WHERE id=?", (batch_id,)).fetchone())


def get_batch(user_id: int, batch_id: str) -> dict:
    with get_db() as db:
        batch = db.execute("SELECT id FROM task_batches WHERE user_id=? AND id=?", (user_id, batch_id)).fetchone()
        if batch is None:
            _error(404, "BATCH_NOT_FOUND", "批次不存在或无权访问")
        rows = db.execute("SELECT * FROM tasks WHERE user_id=? AND batch_id=? ORDER BY created_at,id", (user_id, batch_id)).fetchall()
    return {"id": batch_id, "counts": {state: sum(row["state"] == state for row in rows) for state in ALL_STATES}, "tasks": [_snapshot(row) for row in rows]}


def list_tasks(user_id: int, *, kind: TaskKind | None = None, state: TaskState | None = None, batch_id: str | None = None, limit: int = 20, cursor: str | None = None) -> dict:
    conditions = ["user_id=?"]
    parameters: list = [user_id]
    for field, value in (("kind", kind), ("state", state), ("batch_id", batch_id)):
        if value is not None:
            conditions.append(f"{field}=?")
            parameters.append(value)
    filters = [kind, state, batch_id]
    if cursor:
        try:
            value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError("invalid cursor")
            timestamp, item_id, original_filters = value
            if type(timestamp) is not int or not -(2**63) <= timestamp < 2**63 or not isinstance(item_id, str) or not item_id or original_filters != filters:
                raise ValueError("invalid cursor")
        except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
            raise HTTPException(422, {"code": "INVALID_CURSOR", "error": "任务分页位置无效，请刷新后重试"}) from exc
        conditions.append("(created_at < ? OR (created_at=? AND id < ?))")
        parameters.extend((timestamp, timestamp, item_id))
    with get_db() as db:
        rows = db.execute(f"SELECT * FROM tasks WHERE {' AND '.join(conditions)} ORDER BY created_at DESC,id DESC LIMIT ?", (*parameters, limit + 1)).fetchall()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last["created_at"], last["id"], filters]).encode()).decode().rstrip("=")
    return {"items": [_snapshot(row) for row in rows[:limit]], "next_cursor": next_cursor}


def _update_history(db, task_id: str, state: str, *, filename: str | None = None, error_code: str | None = None):
    db.execute("UPDATE download_history SET status=?,filename=COALESCE(?,filename),error_code=?,updated_at=? WHERE task_id=? AND origin='task'",
               (state, filename, error_code, int(time.time()), task_id))


def cancel_task(user_id: int, task_id: str) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, user_id, task_id)
        state = row["state"]
        if state == "queued":
            state = "cancelled"
            db.execute("UPDATE tasks SET state=?,cancel_requested=1,finished_at=?,version=version+1 WHERE id=?", (state, int(time.time()), task_id))
        elif state == "running":
            state = "cancelling"
            db.execute("UPDATE tasks SET state=?,cancel_requested=1,version=version+1 WHERE id=?", (state, task_id))
        if state != row["state"]:
            _update_history(db, task_id, state)
    if state == "cancelled":
        _cleanup_file(task_id)
    return {"id": task_id, "state": state}


def retry_task(user_id: int, task_id: str, request_key: str) -> dict:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        original = _row(db, user_id, task_id)
        existing = db.execute("SELECT * FROM tasks WHERE user_id=? AND request_key=?", (user_id, request_key)).fetchone()
        if existing:
            if existing["retry_of"] != task_id:
                _error(409, "REQUEST_KEY_CONFLICT", "该请求标识已用于其他任务，请刷新后重试")
            return _snapshot(existing)
        if original["state"] not in {"failed", "cancelled", "interrupted"}:
            _error(409, "TASK_NOT_RETRYABLE", "只有失败、取消或中断的任务可以重试")
        if original["kind"] != "download":
            _error(409, "TASK_KIND_UNAVAILABLE", "字幕任务请通过对应字幕入口重新提交")
        _check_capacity(db, user_id, 1)
        new_id = _insert_task(db, user_id, original["kind"], json.loads(original["input_json"]), request_key, batch_id=original["batch_id"], retry_of=task_id)
        return _snapshot(_row(db, user_id, new_id))


def claim_task(kind: TaskKind = 'download') -> dict | None:
    """短事务领取最早下载任务；并发上限也在同一写事务内检查。"""
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        if _stopping.is_set():
            return None
        limit = 2 if kind == 'download' else 1
        running = db.execute("SELECT COUNT(*) FROM tasks WHERE kind=? AND state IN ('running','cancelling')", (kind,)).fetchone()[0]
        if running >= limit:
            return None
        row = db.execute("""SELECT * FROM tasks AS t WHERE kind=? AND state='queued'
            AND (SELECT COUNT(*) FROM tasks AS active WHERE active.user_id=t.user_id AND active.kind=t.kind
                AND active.state IN ('running','cancelling')) < ? ORDER BY created_at,id LIMIT 1""", (kind,limit)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE tasks SET state='running',stage='extracting',started_at=?,version=version+1 WHERE id=?", (int(time.time()), row["id"]))
        _update_history(db, row["id"], "running")
        return dict(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())


def queue_disk_bytes() -> int:
    """只统计隔离队列目录；下载器的每次 hook 会检查未知长度写入。"""
    if not QUEUE_ROOT.exists():
        return 0
    total = 0
    for path in QUEUE_ROOT.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # 另一个任务可在扫描时完成清理。
            continue
    return total


def run_download_task(task_id: str):
    """由 asyncio.to_thread 调用。取消先落库，最终成功事务再次检查取消。"""
    with get_db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or row["state"] not in {"running", "cancelling"}:
        return
    with _file_lock:
        _executing.add(task_id)
    data = json.loads(row["input_json"])
    deadline = time.monotonic() + DOWNLOAD_BUDGET_SECONDS
    timed_out = False
    last_progress = 0.0
    last_phase = None

    def should_cancel() -> bool:
        nonlocal timed_out
        timed_out = timed_out or time.monotonic() >= deadline
        with get_db() as db:
            current = db.execute("SELECT cancel_requested,state FROM tasks WHERE id=?", (task_id,)).fetchone()
        return timed_out or _stopping.is_set() or current is None or bool(current["cancel_requested"]) or current["state"] == "interrupted"

    def progress(update: dict):
        nonlocal last_progress, last_phase
        if should_cancel():
            raise DownloadCancelled("任务已取消或执行时间已到")
        if queue_disk_bytes() > MAX_QUEUE_BYTES:
            raise DownloadLimitExceeded("后台下载临时文件超过 10 GiB，请领取或等待其他文件过期后重试")
        now = time.monotonic()
        stage = update.get("stage", "downloading")
        phase = (stage,update.get('track_id'))
        if now - last_progress < 0.5 and phase == last_phase:
            return
        last_progress = now
        last_phase = phase
        values = {key: update[key] for key in ("downloaded_bytes", "total_bytes", "total_is_estimate", "speed_bytes_per_second", "eta_seconds", "track_id", "percent") if key in update}
        with get_db() as db:
            db.execute("UPDATE tasks SET stage=?,progress_json=?,version=version+1 WHERE id=? AND state='running' AND cancel_requested=0",
                       (stage, json.dumps(values), task_id))

    result = None
    try:
        if should_cancel():
            raise DownloadCancelled("任务已取消")
        validate_public_url(data["url"])
        if queue_disk_bytes() >= MAX_QUEUE_BYTES:
            raise DownloadLimitExceeded("后台下载临时文件已达到 10 GiB，请稍后重试")
        downloader = VideoDownloader()
        downloader.download_dir = QUEUE_ROOT / task_id
        while not DOWNLOAD_SLOTS.acquire(timeout=0.2):
            if should_cancel():
                raise DownloadCancelled('任务已取消')
        try:
            result = downloader.download(data["url"], data["format_id"], on_progress=progress, should_cancel=should_cancel, max_bytes=MAX_FILE_BYTES)
        finally:
            DOWNLOAD_SLOTS.release()
        path = Path(result["filepath"]).resolve()
        # 下载返回值属于第三方文件边界，不允许队列保存其目录外的文件。
        try:
            relative = path.relative_to((QUEUE_ROOT / task_id).resolve())
        except ValueError as exc:
            raise VideoError("下载文件未保存在任务目录") from exc
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES or queue_disk_bytes() > MAX_QUEUE_BYTES:
            raise DownloadLimitExceeded("后台下载文件超过空间限制")
        if should_cancel():
            raise DownloadCancelled("任务已取消或执行时间已到")
        now = int(time.time())
        with get_db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state,cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
            if current["state"] != "running" or current["cancel_requested"]:
                raise DownloadCancelled("任务已取消")
            db.execute("""UPDATE tasks SET state='succeeded',stage='ready',finished_at=?,file_expires_at=?,
                file_path=?,task_dir=?,filename=?,warning=?,version=version+1 WHERE id=?""",
                (now, now + FILE_RETENTION_SECONDS, str(Path(task_id) / relative), task_id, result["filename"], result["warning"], task_id))
            _update_history(db, task_id, "succeeded", filename=result["filename"])
    except DownloadCancelled:
        _finish_failure(task_id, "failed" if timed_out else "cancelled", "TASK_TIMEOUT" if timed_out else None,
                        "下载超过 30 分钟执行时间，请手动重试" if timed_out else None)
    except DownloadLimitExceeded as exc:
        _finish_failure(task_id, "failed", "DOWNLOAD_LIMIT_EXCEEDED", str(exc))
    except (VideoError, OSError) as exc:
        logger.info("下载任务 %s 失败: %s", task_id, exc)
        _finish_failure(task_id, "failed", "DOWNLOAD_FAILED", str(exc))
    finally:
        try:
            with get_db() as db:
                final = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            with _file_lock:
                _executing.discard(task_id)
        if final is None or final["state"] != "succeeded":
            _cleanup_file(task_id)


def _finish_failure(task_id: str, state: str, error_code: str | None, message: str | None):
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT state,cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        if current is None or current["state"] in TERMINAL_STATES:
            return
        if current["cancel_requested"]:
            state, error_code, message = "cancelled", None, None
        db.execute("UPDATE tasks SET state=?,error_code=?,error_message=?,finished_at=?,version=version+1 WHERE id=?",
                   (state, error_code, message, int(time.time()), task_id))
        _update_history(db, task_id, state, error_code=error_code)


finish_task_failure = _finish_failure


def _cleanup_file(task_id: str):
    with _file_lock:
        if _file_leases.get(task_id, 0) or task_id in _executing:
            _pending_cleanup.add(task_id)
            return
        target = (QUEUE_ROOT / task_id).resolve()
        if target.parent != QUEUE_ROOT.resolve():
            raise ValueError("任务文件目录必须属于队列目录")
        try:
            if target.exists():
                shutil.rmtree(target)
        except OSError:
            logger.exception("任务 %s 文件暂时无法清理，将在维护时重试", task_id)
            _pending_cleanup.add(task_id)
            return
        with get_db() as db:
            db.execute("UPDATE tasks SET file_path=NULL,task_dir=NULL,version=version+1 WHERE id=? AND (file_path IS NOT NULL OR task_dir IS NOT NULL)", (task_id,))
        _pending_cleanup.discard(task_id)


def remove_task_file(user_id: int, task_id: str):
    """删除本人终态历史时调用；HTTP 发送中的文件延迟到发送结束再清理。"""
    with get_db() as db:
        row = db.execute('SELECT state FROM tasks WHERE user_id=? AND id=?',(user_id,task_id)).fetchone()
        if row is None:
            return
        if row["state"] not in TERMINAL_STATES:
            _error(409, "SOURCE_BUSY", "任务仍在处理中，请先取消后再删除历史")
    _cleanup_file(task_id)


def acquire_task_file(user_id: int, task_id: str) -> dict:
    with _file_lock, get_db() as db:
        row = _row(db, user_id, task_id)
        if row["kind"] != "download" or row["state"] != "succeeded":
            _error(409, "TASK_FILE_NOT_READY", "下载文件尚未准备完成")
        if not row["file_path"] or not row["file_expires_at"] or row["file_expires_at"] <= time.time():
            _error(410, "TASK_FILE_EXPIRED", "下载文件已过期，请重新下载")
        path = (QUEUE_ROOT / row["file_path"]).resolve()
        if not path.is_relative_to((QUEUE_ROOT / task_id).resolve()):
            raise ValueError("任务文件必须属于该任务目录")
        if not path.is_file():
            _error(410, "TASK_FILE_EXPIRED", "下载文件已清理，请重新下载")
        _file_leases[task_id] = _file_leases.get(task_id, 0) + 1
        return {"path": path, "filename": row["filename"], "warning": row["warning"]}


def release_task_file(task_id: str):
    with _file_lock:
        remaining = _file_leases[task_id] - 1
        if remaining:
            _file_leases[task_id] = remaining
        else:
            del _file_leases[task_id]
            if task_id in _pending_cleanup:
                _cleanup_file(task_id)


def cleanup_task_files():
    """过期文件与失败残留可重试清理；正在发送的文件等发送结束后处理。"""
    now = int(time.time())
    with get_db() as db:
        rows = db.execute("""SELECT id FROM tasks WHERE state IN ('failed','cancelled','interrupted')
            OR (state='succeeded' AND file_expires_at IS NOT NULL AND file_expires_at<=?)""", (now,)).fetchall()
        known = {row[0] for row in db.execute("SELECT id FROM tasks")}
    for row in rows:
        _cleanup_file(row["id"])
    for task_id in list(_pending_cleanup):
        _cleanup_file(task_id)
    with _file_lock, get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT id FROM tasks WHERE state IN ('succeeded','failed','cancelled','interrupted') AND finished_at<?", (now - SNAPSHOT_RETENTION_SECONDS,)).fetchall()
        for row in old:
            if not _file_leases.get(row["id"], 0) and row["id"] not in _pending_cleanup:
                db.execute("DELETE FROM tasks WHERE id=?", (row["id"],))
    if QUEUE_ROOT.exists():
        for directory in QUEUE_ROOT.iterdir():
            if directory.is_dir() and directory.name not in known and now - directory.stat().st_mtime > FILE_RETENTION_SECONDS:
                _cleanup_file(directory.name)


def recover_interrupted_tasks():
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        ids = [row[0] for row in db.execute("SELECT id FROM tasks WHERE state IN ('running','cancelling')")]
        db.execute("""UPDATE tasks SET state='interrupted',cancel_requested=1,finished_at=?,error_code='TASK_INTERRUPTED',
            error_message='服务重启或停止，任务已中断，请手动重试',version=version+1 WHERE state IN ('running','cancelling')""", (int(time.time()),))
        for task_id in ids:
            _update_history(db, task_id, "interrupted", error_code="TASK_INTERRUPTED")
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND task_id IN (SELECT id FROM tasks WHERE state IN ('interrupted','cancelled','failed'))",(int(time.time()),))
    for task_id in ids:
        _cleanup_file(task_id)


async def _worker(kind: TaskKind = 'download'):
    pending_failure = None
    while not _stopping.is_set() or pending_failure is not None:
        row = None
        try:
            if pending_failure is not None:
                # 下载不能因数据库短锁自动再执行一次；库恢复后明确结束该任务。
                await asyncio.to_thread(_finish_failure, pending_failure, "interrupted", "TASK_DATABASE_UNAVAILABLE", "任务保存时数据库暂不可用，请手动重试")
                await asyncio.to_thread(cleanup_task_files)
                pending_failure = None
                continue
            row = await asyncio.to_thread(claim_task) if kind == 'download' else await asyncio.to_thread(claim_task,kind)
            if row:
                if kind == 'download':
                    await asyncio.to_thread(run_download_task, row['id'])
                elif kind == 'translation':
                    from .subtitles import run_translation_task
                    await asyncio.to_thread(run_translation_task,row['id'])
                else:
                    from .transcriber import run_transcription_task
                    await asyncio.to_thread(run_transcription_task,row['id'])
            else:
                await asyncio.sleep(0.2)
        except sqlite3.OperationalError:
            logger.exception("任务数据库暂不可用，调度将在稍后重试")
            if row is not None:
                pending_failure = row["id"]
            await asyncio.sleep(1)
        except Exception:
            # 非预期内部错误保存明确失败后继续抛出，由宿主日志暴露原因。
            logger.exception("任务执行出现内部错误，工作线程已停止")
            if row is not None:
                await asyncio.to_thread(_finish_failure, row["id"], "failed", "TASK_INTERNAL_ERROR", "任务执行出现内部错误，请查看服务日志")
            raise


async def _maintain():
    while not _stopping.is_set():
        try:
            await asyncio.to_thread(cleanup_task_files)
        except sqlite3.OperationalError:
            logger.exception("任务文件维护时数据库暂不可用，将在下一轮重试")
        await asyncio.sleep(60)


async def start_workers():
    global _accepting, _maintenance
    if _workers:
        return False
    await asyncio.to_thread(recover_interrupted_tasks)
    await asyncio.to_thread(cleanup_task_files)
    _stopping.clear()
    _accepting = True
    _workers.extend(asyncio.create_task(_worker(), name=f"download-worker-{index}") for index in range(2))
    _workers.append(asyncio.create_task(_worker('translation'),name='translation-worker'))
    _workers.append(asyncio.create_task(_worker('transcription'),name='transcription-worker'))
    _maintenance = asyncio.create_task(_maintain(), name="task-file-maintenance")
    return True


async def stop_workers():
    global _accepting, _maintenance
    _accepting = False
    _stopping.set()
    # 先禁止成功提交，再等阻塞下载返回；不声称能够强杀线程或 ffmpeg。
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id FROM tasks WHERE state IN ('running','cancelling')").fetchall()
        db.execute("""UPDATE tasks SET state='interrupted',cancel_requested=1,finished_at=?,error_code='TASK_INTERRUPTED',
            error_message='服务正在停止，请稍后手动重试',version=version+1 WHERE state IN ('running','cancelling')""", (int(time.time()),))
        for row in rows:
            _update_history(db, row["id"], "interrupted", error_code="TASK_INTERRUPTED")
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND task_id IN (SELECT id FROM tasks WHERE state IN ('interrupted','cancelled','failed'))",(int(time.time()),))
    if _maintenance:
        _maintenance.cancel()
        try:
            await _maintenance
        except asyncio.CancelledError:
            pass
        _maintenance = None
    workers = list(_workers)
    _workers.clear()
    if workers:
        await asyncio.gather(*workers)
    await asyncio.to_thread(cleanup_task_files)
