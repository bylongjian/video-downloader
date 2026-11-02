"""翻译按次、转写按秒独立计额；成功结算使用结果保存的同一连接。"""

import secrets
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import time
from typing import Literal

from .database import get_db
from .quota import QuotaError, SHANGHAI

ExtensionKind = Literal["translation", "transcription"]
LIMITS = {"translation": 3, "transcription": 3600}
RESERVATION_SECONDS = {"translation": 15 * 60, "transcription": 30 * 60}


@dataclass(frozen=True)
class ExtensionReservation:
    request_id: str
    user_id: int
    kind: ExtensionKind
    quota_date: str
    expires_at: int
    amount: int


def _snapshot(db, user_id: int, now: int) -> dict:
    from .membership import get_entitlements
    limits = get_entitlements(user_id,now,db)['limits']
    current = datetime.fromtimestamp(now, SHANGHAI)
    date = current.date().isoformat()
    result = {"date": date, "timezone": "Asia/Shanghai", "server_time": current.isoformat(),
              "reset_at": (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}
    counts = db.execute("""SELECT kind,
        SUM(CASE WHEN status='succeeded' THEN actual_amount ELSE 0 END) AS used,
        SUM(CASE WHEN status='reserved' AND expires_at>? THEN reserved_amount ELSE 0 END) AS reserved
        FROM extension_usage_requests WHERE user_id=? AND quota_date=? GROUP BY kind""", (now, user_id, date)).fetchall()
    for kind in LIMITS:
        limit = limits[kind]
        result[kind] = {"limit": limit, "used": 0, "reserved": 0, "remaining": limit,
                        "unit": "requests" if kind == "translation" else "seconds"}
    for row in counts:
        usage = result[row["kind"]]
        usage.update(used=row["used"], reserved=row["reserved"], remaining=max(0, usage["limit"] - row["used"] - row["reserved"]))
    return result


def get_extension_quota(user_id: int, *, db=None, now: int | None=None) -> dict:
    with nullcontext(db) if db is not None else get_db() as connection:
        return _snapshot(connection, user_id, int(time()) if now is None else now)


def release_expired_extension_usage():
    now = int(time())
    with get_db() as db:
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND expires_at<=?", (now, now))


def reserve_extension_usage(user_id: int, kind: ExtensionKind, amount: int, task_id: str) -> ExtensionReservation:
    with get_db() as db, db:
        db.execute("BEGIN IMMEDIATE")
        now = int(time())
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND expires_at<=?", (now, now))
        existing = db.execute("SELECT * FROM extension_usage_requests WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            if existing["user_id"] != user_id or existing["kind"] != kind or existing["reserved_amount"] != amount or existing["status"] != "reserved":
                raise QuotaError("QUOTA_COMMIT_FAILED", "该任务的额度预占已结束，请重新提交任务")
            return ExtensionReservation(existing["request_id"], user_id, kind, existing["quota_date"], existing["expires_at"], amount)
        snapshot = _snapshot(db, user_id, now)
        usage = snapshot[kind]
        if usage["remaining"] < amount:
            exhausted = usage["used"] + amount > usage["limit"]
            raise QuotaError("QUOTA_EXHAUSTED" if exhausted else "QUOTA_BUSY",
                             "今日额度不足，请在北京时间零点后重试" if exhausted else "可用额度正在处理中，请等待后刷新", snapshot)
        reservation = ExtensionReservation(secrets.token_urlsafe(24), user_id, kind, snapshot["date"], now + RESERVATION_SECONDS[kind], amount)
        db.execute("""INSERT INTO extension_usage_requests(request_id,user_id,kind,quota_date,reserved_amount,
            status,created_at,expires_at,task_id) VALUES(?,?,?,?,?,'reserved',?,?,?)""",
                   (reservation.request_id, user_id, kind, reservation.quota_date, amount, now, reservation.expires_at, task_id))
        return reservation


def commit_extension_usage(request_id: str, db) -> dict:
    """调用方已开启写事务；结果或任务终态写入失败时，这次计额一起回滚。"""
    now = int(time())
    row = db.execute("SELECT * FROM extension_usage_requests WHERE request_id=?", (request_id,)).fetchone()
    if row is None or row["status"] == "released" or (row["status"] == "reserved" and row["expires_at"] <= now):
        raise QuotaError("QUOTA_COMMIT_FAILED", "本次请求已过期或取消，未计入成功额度，请重新发起")
    if row["status"] == "succeeded":
        return _snapshot(db, row["user_id"], now)
    task = db.execute("SELECT state,cancel_requested FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
    if task and (task["state"] != "running" or task["cancel_requested"]):
        raise QuotaError("QUOTA_COMMIT_FAILED", "任务已取消或中断，未计入成功额度")
    db.execute("UPDATE extension_usage_requests SET status='succeeded',actual_amount=reserved_amount,completed_at=? WHERE request_id=?", (now, request_id))
    return _snapshot(db, row["user_id"], now)


def release_extension_usage(request_id: str):
    with get_db() as db:
        db.execute("UPDATE extension_usage_requests SET status='released',completed_at=? WHERE request_id=? AND status='reserved'", (int(time()), request_id))
