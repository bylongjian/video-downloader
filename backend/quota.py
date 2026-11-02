"""先在短事务内预占，完整成功后结算；不在模型运行时持锁。"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import time
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import Depends
from anyio.from_thread import check_cancelled

from .auth import AuthenticatedUser, get_current_user

from .database import get_db
from . import database

QuotaKind = Literal['summary', 'chat']
LIMITS = {'summary': 3, 'chat': 10}
SHANGHAI = ZoneInfo('Asia/Shanghai')
RESERVATION_SECONDS = 15 * 60


@dataclass(frozen=True)
class UsageReservation:
    request_id: str
    user_id: int
    kind: QuotaKind
    quota_date: str
    expires_at: int


class QuotaError(Exception):
    def __init__(self, code: str, message: str, quota: dict | None = None):
        super().__init__(message)
        self.code = code
        self.quota = quota


def _snapshot(db, user_id: int, now: int) -> dict:
    from .membership import get_entitlements
    entitlement = get_entitlements(user_id,now,db)
    limits = entitlement['limits']
    current = datetime.fromtimestamp(now, SHANGHAI)
    date = current.date().isoformat()
    result = {'date': date, 'timezone': 'Asia/Shanghai', 'server_time': current.isoformat(),
              'reset_at': (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}
    counts = db.execute("""SELECT kind,
        SUM(status='succeeded') AS used,
        SUM(status='reserved' AND expires_at>?) AS reserved
        FROM ai_usage_requests WHERE user_id=? AND quota_date=? GROUP BY kind""", (now, user_id, date)).fetchall()
    for kind in LIMITS:
        limit = limits[kind]
        result[kind] = {'limit': limit, 'used': 0, 'reserved': 0, 'remaining': limit}
    for row in counts:
        limit = limits[row['kind']]
        result[row['kind']] = {'limit': limit, 'used': row['used'], 'reserved': row['reserved'], 'remaining': max(0,limit - row['used'] - row['reserved'])}
    from .extension_quota import get_extension_quota
    extras = get_extension_quota(user_id,db=db,now=now)
    result.update(translation=extras['translation'],transcription=extras['transcription'])
    result['membership'] = {'tier':entitlement['tier'],'payment_mode':entitlement['payment_mode'],
        'expires_at':datetime.fromtimestamp(entitlement['expires_at'],SHANGHAI).isoformat() if entitlement['expires_at'] is not None else None}
    return result


def get_quota(user_id: int) -> dict:
    with get_db() as db:
        return _snapshot(db, user_id, int(time()))


def release_expired_usage():
    now = int(time())
    with get_db() as db:
        db.execute("UPDATE ai_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND expires_at<=?", (now, now))


def reserve_usage(user_id: int, kind: QuotaKind) -> UsageReservation:
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        now = int(time())
        db.execute("UPDATE ai_usage_requests SET status='released',completed_at=? WHERE status='reserved' AND expires_at<=?", (now, now))
        snapshot = _snapshot(db, user_id, now)
        usage = snapshot[kind]
        if usage['remaining'] <= 0:
            exhausted = usage['used'] >= usage['limit']
            raise QuotaError('QUOTA_EXHAUSTED' if exhausted else 'QUOTA_BUSY',
                             '今日次数已用完，请在北京时间零点后重试' if exhausted else '可用名额正在处理中，请等待后刷新', snapshot)
        reservation = UsageReservation(secrets.token_urlsafe(24), user_id, kind, snapshot['date'], now + RESERVATION_SECONDS)
        db.execute("INSERT INTO ai_usage_requests(request_id,user_id,kind,quota_date,status,created_at,expires_at) VALUES(?,?,?,?,'reserved',?,?)",
                   (reservation.request_id, user_id, kind, reservation.quota_date, now, reservation.expires_at))
        return reservation


def commit_usage(request_id: str, *, summary_result: dict | None = None) -> dict:
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        now = int(time())
        row = db.execute('SELECT * FROM ai_usage_requests WHERE request_id=?', (request_id,)).fetchone()
        if row['status'] == 'succeeded':
            return _snapshot(db, row['user_id'], now)
        if row['status'] != 'reserved' or row['expires_at'] <= now:
            raise QuotaError('QUOTA_COMMIT_FAILED', '本次请求已过期或取消，未计入成功次数，请重新发起')
        db.execute("UPDATE ai_usage_requests SET status='succeeded',completed_at=? WHERE request_id=?", (now, request_id))
        if summary_result is not None:
            database.save_summary_result(db, row['user_id'], summary_result, now)
        return _snapshot(db, row['user_id'], now)


def release_usage(request_id: str):
    with get_db() as db:
        db.execute("UPDATE ai_usage_requests SET status='released',completed_at=? WHERE request_id=? AND status='reserved'", (int(time()), request_id))


def ensure_active(reservation: UsageReservation):
    check_cancelled()
    if time() >= reservation.expires_at:
        raise QuotaError('QUOTA_COMMIT_FAILED', '本次生成超过处理期限，未计入成功次数，请重试')


def reserve_summary_usage(user: AuthenticatedUser = Depends(get_current_user)):
    reservation = reserve_usage(user.id, 'summary')
    try:
        yield reservation
    finally:
        release_usage(reservation.request_id)


def reserve_chat_usage(user: AuthenticatedUser = Depends(get_current_user)):
    reservation = reserve_usage(user.id, 'chat')
    try:
        yield reservation
    finally:
        release_usage(reservation.request_id)
