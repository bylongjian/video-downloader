"""会员期限只从当前支付环境中尚未撤销的成功订单计算。"""

import os
import sqlite3

from .database import get_db

FREE_LIMITS = {'summary': 3, 'chat': 10, 'translation': 3, 'transcription': 3600}
VIP_LIMITS = {'summary': 30, 'chat': 100, 'translation': 20, 'transcription': 14400}
TRANSCRIPTION_SINGLE_LIMIT = 1800


def get_entitlements(user_id: int, now: int, db: sqlite3.Connection | None = None) -> dict:
    if db is None:
        with get_db() as connection:
            return get_entitlements(user_id, now, connection)
    mode = os.getenv('PAYMENT_MODE', 'test')
    expiry = None
    for row in db.execute('''SELECT paid_at,duration_seconds FROM membership_grants
            WHERE user_id=? AND payment_mode=? AND revoked_at IS NULL AND paid_at<=?
            ORDER BY paid_at,order_id''', (user_id, mode, now)):
        expiry = max(expiry or 0, row['paid_at']) + row['duration_seconds']
    active = expiry is not None and expiry > now
    return {'tier': 'vip' if active else 'free', 'expires_at': expiry, 'payment_mode': mode,
            'limits': dict(VIP_LIMITS if active else FREE_LIMITS),
            'transcription_single_limit': TRANSCRIPTION_SINGLE_LIMIT}


def grant_membership(db: sqlite3.Connection, order: sqlite3.Row):
    db.execute('''INSERT INTO membership_grants(order_id,user_id,payment_mode,paid_at,duration_seconds)
        VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO NOTHING''',
        (order['id'], order['user_id'], order['payment_mode'], order['paid_at'], order['duration_seconds']))


def revoke_membership(db: sqlite3.Connection, order_id: str, now: int):
    db.execute('UPDATE membership_grants SET revoked_at=COALESCE(revoked_at,?) WHERE order_id=?', (now, order_id))
