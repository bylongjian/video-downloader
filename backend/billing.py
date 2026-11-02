"""固定服务端套餐、Stripe Checkout 和可重放的已验签支付事件。"""

import base64
import binascii
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import stripe
from fastapi import HTTPException

from .database import get_db
from .membership import VIP_LIMITS, get_entitlements, grant_membership, revoke_membership

PLAN_CODE = 'vip_30d'
AMOUNT_MINOR = 990
CURRENCY = 'cny'
DURATION_SECONDS = 30 * 86400
PAID_EVENTS = {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}
FAILED_EVENTS = {'checkout.session.expired', 'checkout.session.async_payment_failed'}
REVOCATION_EVENTS = {'charge.refunded', 'charge.dispute.created', 'charge.dispute.closed'}


@dataclass(frozen=True)
class PaymentSettings:
    mode: str
    secret_key: str
    webhook_secret: str
    price_id: str
    frontend_url: str
    available: bool


def _settings() -> PaymentSettings:
    mode = os.getenv('PAYMENT_MODE', 'test')
    secret = os.getenv('STRIPE_SECRET_KEY', '')
    webhook = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    price = os.getenv('STRIPE_PRICE_ID_VIP_30D', '')
    frontend = os.getenv('FRONTEND_URL', '').rstrip('/')
    parsed = urlsplit(frontend)
    available = (mode in {'test', 'live'} and secret.startswith(f'sk_{mode}_') and
                 webhook.startswith('whsec_') and price.startswith('price_') and
                 parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and
                 parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment and
                 (mode == 'test' or os.getenv('PAYMENT_LIVE_ENABLED', 'false').lower() == 'true'))
    return PaymentSettings(mode, secret, webhook, price, frontend, available)


def _require_settings() -> PaymentSettings:
    settings = _settings()
    if not settings.available:
        raise HTTPException(503, {'code': 'PAYMENT_NOT_CONFIGURED', 'error': '支付尚未配置完成，当前无法购买会员'})
    return settings


def _stripe_client(settings: PaymentSettings):
    return stripe.StripeClient(settings.secret_key, max_network_retries=1,
                               http_client=stripe.RequestsClient(timeout=15))


def _iso(stamp: int | None) -> str | None:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def list_plans() -> dict:
    settings = _settings()
    return {'payment_mode': settings.mode, 'available': settings.available,
            'plans': [{'code': PLAN_CODE, 'amount_minor': AMOUNT_MINOR, 'currency': CURRENCY,
                       'duration_days': 30, 'limits': dict(VIP_LIMITS), 'transcription_single_limit': 1800}]}


def _order_response(row, entitlement: dict) -> dict:
    return {**{key: row[key] for key in ('id', 'status', 'plan_code', 'amount_minor', 'currency', 'payment_mode')},
            'created_at': _iso(row['created_at']), 'paid_at': _iso(row['paid_at']),
            'entitlement': {**entitlement, 'expires_at': _iso(entitlement['expires_at'])}}


def get_order(user_id: int, order_id: str) -> dict:
    with get_db() as db:
        row = db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?', (order_id, user_id)).fetchone()
        if row is None:
            raise HTTPException(404, {'code': 'ORDER_NOT_FOUND', 'error': '订单不存在或无权访问'})
        return _order_response(row, get_entitlements(user_id, int(time.time()), db))


def list_orders(user_id: int, limit: int = 20, cursor: str | None = None) -> dict:
    params = [user_id]
    after = ''
    if cursor is not None:
        try:
            position = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
            if (not isinstance(position, list) or len(position) != 2 or type(position[0]) is not int
                    or not -(2**63) <= position[0] < 2**63 or not isinstance(position[1], str)):
                raise ValueError('invalid cursor')
            after = ' AND (created_at<? OR (created_at=? AND id<?))'
            params.extend((position[0], position[0], position[1]))
        except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
            raise HTTPException(422, {'code': 'INVALID_CURSOR', 'error': '订单分页位置无效，请刷新重试'}) from exc
    with get_db() as db:
        rows = db.execute(f'''SELECT * FROM payment_orders WHERE user_id=?{after}
            ORDER BY created_at DESC,id DESC LIMIT ?''', (*params, limit + 1)).fetchall()
        entitlement = get_entitlements(user_id, int(time.time()), db)
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last['created_at'], last['id']]).encode()).decode().rstrip('=')
    return {'items': [_order_response(row, entitlement) for row in rows[:limit]], 'next_cursor': next_cursor}


def _checkout_response(row) -> dict:
    return {'order_id': row['id'], 'checkout_url': row['checkout_url'], 'payment_mode': row['payment_mode']}


def create_checkout(user_id: int, request_key: str, plan_code: str) -> dict:
    settings = _require_settings()
    if plan_code != PLAN_CODE:
        raise HTTPException(422, {'code': 'INVALID_PLAN', 'error': '请选择有效会员套餐'})
    now = int(time.time())
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM payment_orders WHERE user_id=? AND request_key=?', (user_id, request_key)).fetchone()
        if row is not None:
            if row['payment_mode'] != settings.mode or row['plan_code'] != plan_code:
                raise HTTPException(409, {'code': 'CHECKOUT_KEY_CONFLICT', 'error': '下单标识已用于其他支付环境或套餐，请重新购买'})
            if row['checkout_url']:
                return _checkout_response(row)
            if row['status'] != 'creating':
                raise HTTPException(409, {'code': 'ORDER_NOT_PAYABLE', 'error': '此订单已结束，请重新购买'})
        else:
            order_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO payment_orders(id,user_id,request_key,plan_code,amount_minor,currency,
                duration_seconds,provider,payment_mode,price_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'stripe',?,?,'creating',?,?)''',
                (order_id, user_id, request_key, PLAN_CODE, AMOUNT_MINOR, CURRENCY, DURATION_SECONDS,
                 settings.mode, settings.price_id, now, now))
            row = db.execute('SELECT * FROM payment_orders WHERE id=?', (order_id,)).fetchone()
    provider = _stripe_client(settings)
    try:
        price = provider.v1.prices.retrieve(row['price_id']).to_dict()
        if not (price['id'] == row['price_id'] and price.get('active') is True and
                price.get('livemode') is (settings.mode == 'live') and price.get('type') == 'one_time' and
                price.get('unit_amount') == row['amount_minor'] and price.get('currency') == row['currency']):
            with get_db() as db:
                db.execute("UPDATE payment_orders SET status='failed',updated_at=? WHERE id=? AND status='creating'", (now, row['id']))
            raise HTTPException(503, {'code': 'PAYMENT_PRICE_MISMATCH', 'error': '会员价格配置不一致，请联系管理员'})
        session = provider.v1.checkout.sessions.create(params={
            'mode': 'payment', 'line_items': [{'price': row['price_id'], 'quantity': 1}],
            'client_reference_id': row['id'],
            'metadata': {'order_id': row['id'], 'plan_code': row['plan_code'], 'price_id': row['price_id']},
            'success_url': settings.frontend_url + '/billing?order_id=' + row['id'],
            'cancel_url': settings.frontend_url + '/billing?order_id=' + row['id'] + '&checkout=cancelled',
        }, options={'idempotency_key': 'saveany:' + settings.mode + ':' + row['id']}).to_dict()
    except (stripe.InvalidRequestError, stripe.AuthenticationError, stripe.PermissionError) as exc:
        with get_db() as db:
            db.execute("UPDATE payment_orders SET status='failed',updated_at=? WHERE id=? AND status='creating'", (now, row['id']))
        raise HTTPException(503, {'code': 'PAYMENT_PROVIDER_REJECTED', 'error': '支付服务拒绝创建订单，请检查支付配置'}) from exc
    except stripe.StripeError as exc:
        # 网络超时/限流/上游故障不能断言未创建；重试仍使用原订单和相同 provider key。
        raise HTTPException(503, {'code': 'PAYMENT_PROVIDER_UNAVAILABLE', 'error': '支付服务暂不可用，请重试原订单'}) from exc
    parsed = urlsplit(session['url'])
    if session.get('livemode') is not (settings.mode == 'live') or parsed.scheme != 'https' or parsed.hostname != 'checkout.stripe.com':
        raise HTTPException(503, {'code': 'PAYMENT_SESSION_INVALID', 'error': '支付会话配置不一致，请联系管理员'})
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''UPDATE payment_orders SET checkout_session_id=?,checkout_url=?,updated_at=?,
            status=CASE WHEN status='creating' THEN 'pending' ELSE status END WHERE id=?''',
            (session['id'], session['url'], now, row['id']))
        _replay_pending(db, now)
        result = db.execute('SELECT * FROM payment_orders WHERE id=?', (row['id'],)).fetchone()
    return _checkout_response(result)


def _object_reference(value):
    # Stripe 的展开对象和字符串 ID 都是支付商返回边界允许的格式。
    return value.get('id') if isinstance(value, dict) else value


def _event_summary(event: dict, provider) -> dict:
    obj = event['data']['object']
    kind = event['type']
    summary = {'object_id': obj['id'], 'created': event['created'], 'livemode': obj.get('livemode', event['livemode'])}
    if kind.startswith('checkout.session.'):
        metadata = obj.get('metadata') or {}
        summary.update(session_id=obj['id'], payment_intent_id=_object_reference(obj.get('payment_intent')),
                       order_hint=metadata.get('order_id'), plan_code=metadata.get('plan_code'),
                       price_id=metadata.get('price_id'), client_reference_id=obj.get('client_reference_id'),
                       mode=obj.get('mode'), payment_status=obj.get('payment_status'),
                       amount=obj.get('amount_total'), currency=obj.get('currency'))
        if kind in PAID_EVENTS and obj.get('payment_status') == 'paid':
            remote = provider.v1.checkout.sessions.retrieve(obj['id'], params={'expand': ['line_items.data.price']}).to_dict()
            lines = remote.get('line_items', {}).get('data', [])
            summary['verified_session'] = {
                'id': remote['id'], 'livemode': remote['livemode'], 'mode': remote['mode'],
                'payment_status': remote['payment_status'], 'amount': remote.get('amount_total'),
                'currency': remote.get('currency'), 'payment_intent_id': _object_reference(remote.get('payment_intent')),
                'client_reference_id': remote.get('client_reference_id'),
                'metadata': {key: remote.get('metadata', {}).get(key) for key in ('order_id', 'plan_code', 'price_id')},
                'line_items': [{'price_id': _object_reference(line.get('price')), 'quantity': line.get('quantity')} for line in lines],
                'has_more': remote.get('line_items', {}).get('has_more', False),
            }
    else:
        summary.update(payment_intent_id=_object_reference(obj.get('payment_intent')),
                       amount=obj.get('amount'), amount_refunded=obj.get('amount_refunded'),
                       currency=obj.get('currency'), refunded=obj.get('refunded'), dispute_status=obj.get('status'))
    return summary


def process_stripe_event(payload: bytes, signature: str):
    settings = _require_settings()
    try:
        stripe.Webhook.construct_event(payload, signature, settings.webhook_secret)
        event = json.loads(payload)
        if (not isinstance(event, dict) or not isinstance(event.get('id'), str) or
                not isinstance(event.get('type'), str) or type(event.get('livemode')) is not bool or
                type(event.get('created')) is not int or not isinstance(event.get('data'), dict) or
                not isinstance(event['data'].get('object'), dict)):
            raise ValueError('invalid event')
    except (ValueError, KeyError, TypeError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(400, {'code': 'PAYMENT_SIGNATURE_INVALID', 'error': '支付回调签名或内容无效'}) from exc
    if event['livemode'] is not (settings.mode == 'live'):
        raise HTTPException(400, {'code': 'PAYMENT_ENVIRONMENT_MISMATCH', 'error': '支付回调环境不匹配'})
    with get_db() as db:
        previous = db.execute('SELECT status FROM payment_events WHERE event_id=?', (event['id'],)).fetchone()
    if previous is not None and previous['status'] != 'pending':
        return
    kind = event['type']
    supported = kind in PAID_EVENTS | FAILED_EVENTS | REVOCATION_EVENTS
    try:
        summary = _event_summary(event, _stripe_client(settings)) if supported else {'object_id': event['data']['object'].get('id')}
    except stripe.StripeError as exc:
        raise HTTPException(503, {'code': 'PAYMENT_PROVIDER_UNAVAILABLE', 'error': '付款资料暂未确认，请稍后重试'}) from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(400, {'code': 'PAYMENT_EVENT_INVALID', 'error': '付款资料不完整'}) from exc
    now = int(time.time())
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT INTO payment_events(event_id,event_type,payment_mode,provider_object_id,
            checkout_session_id,payment_intent_id,event_summary_json,status,received_at,processed_at,outcome)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING''',
            (event['id'], kind, settings.mode, summary.get('object_id'), summary.get('session_id'),
             summary.get('payment_intent_id'), json.dumps(summary, separators=(',', ':')),
             'pending' if supported else 'ignored', now, None if supported else now, None if supported else 'unsupported'))
        _replay_pending(db, now)


def replay_pending_events():
    with get_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        _replay_pending(db, int(time.time()))


def _find_order(db, event, summary):
    if event['checkout_session_id']:
        return db.execute('SELECT * FROM payment_orders WHERE checkout_session_id=? AND payment_mode=?',
                          (event['checkout_session_id'], event['payment_mode'])).fetchone()
    if event['payment_intent_id']:
        return db.execute('SELECT * FROM payment_orders WHERE payment_intent_id=? AND payment_mode=?',
                          (event['payment_intent_id'], event['payment_mode'])).fetchone()
    return None


def _finish_event(db, event_id, order_id, now, outcome, status='processed'):
    db.execute('''UPDATE payment_events SET order_id=?,status=?,processed_at=?,outcome=? WHERE event_id=?''',
               (order_id, status, now, outcome, event_id))


def _payment_matches(order, summary) -> bool:
    remote = summary.get('verified_session')
    expected = {'order_id': order['id'], 'plan_code': order['plan_code'], 'price_id': order['price_id']}
    return bool(remote and summary['livemode'] is (order['payment_mode'] == 'live') and
                summary['order_hint'] == order['id'] and summary['client_reference_id'] == order['id'] and
                summary['plan_code'] == order['plan_code'] and summary['price_id'] == order['price_id'] and
                summary['mode'] == 'payment' and summary['payment_status'] == 'paid' and
                summary['amount'] == order['amount_minor'] and summary['currency'] == order['currency'] and
                remote['id'] == order['checkout_session_id'] and remote['livemode'] is (order['payment_mode'] == 'live') and
                remote['mode'] == 'payment' and remote['payment_status'] == 'paid' and
                remote['amount'] == order['amount_minor'] and remote['currency'] == order['currency'] and
                remote['payment_intent_id'] == summary['payment_intent_id'] and bool(summary['payment_intent_id']) and
                order['payment_intent_id'] in (None, summary['payment_intent_id']) and
                remote['client_reference_id'] == order['id'] and remote['metadata'] == expected and
                remote['has_more'] is False and remote['line_items'] == [{'price_id': order['price_id'], 'quantity': 1}])


def _replay_pending(db, now: int):
    # 先绑定已确认的 PaymentIntent，再处理撤销，最后授予；退款先到也不会重新开通。
    events = db.execute("SELECT * FROM payment_events WHERE status='pending' ORDER BY received_at,event_id").fetchall()
    for event in events:
        if event['event_type'] not in PAID_EVENTS:
            continue
        summary = json.loads(event['event_summary_json'])
        order = _find_order(db, event, summary)
        if order is not None and _payment_matches(order, summary):
            db.execute('UPDATE payment_orders SET payment_intent_id=? WHERE id=? AND payment_intent_id IS NULL',
                       (summary['payment_intent_id'], order['id']))
    for event in sorted(events, key=lambda item: item['event_type'] not in REVOCATION_EVENTS):
        summary = json.loads(event['event_summary_json'])
        order = _find_order(db, event, summary)
        if order is None:
            if event['received_at'] < now - 7 * 86400:
                db.execute("UPDATE payment_events SET outcome='manual_review_required' WHERE event_id=?", (event['event_id'],))
            continue
        kind = event['event_type']
        outcome = 'ignored'
        if kind in PAID_EVENTS:
            if not _payment_matches(order, summary):
                outcome = 'payment_mismatch' if summary.get('payment_status') == 'paid' else 'not_paid'
            elif order['status'] in {'refunded', 'disputed'}:
                outcome = 'already_revoked'
            else:
                db.execute("""UPDATE payment_orders SET status=CASE WHEN status='review_required' THEN status ELSE 'paid' END,
                    paid_at=COALESCE(paid_at,?),updated_at=? WHERE id=? AND status NOT IN ('refunded','disputed')""",
                           (summary['created'], now, order['id']))
                grant_membership(db, db.execute('SELECT * FROM payment_orders WHERE id=?', (order['id'],)).fetchone())
                outcome = 'paid'
        elif kind in FAILED_EVENTS:
            status = 'expired' if kind.endswith('expired') else 'failed'
            db.execute("UPDATE payment_orders SET status=?,updated_at=? WHERE id=? AND status IN ('creating','pending')", (status, now, order['id']))
            outcome = status
        elif (summary['currency'] != order['currency'] or type(summary['amount']) is not int or
              not 0 < summary['amount'] <= order['amount_minor'] or
              (kind == 'charge.refunded' and summary['amount'] != order['amount_minor']) or
              summary['livemode'] is not (order['payment_mode'] == 'live')):
            outcome = 'revocation_mismatch'
        elif kind == 'charge.refunded':
            if summary['refunded'] is True and summary['amount_refunded'] == order['amount_minor']:
                db.execute("UPDATE payment_orders SET status='refunded',updated_at=? WHERE id=?", (now, order['id']))
                revoke_membership(db, order['id'], now)
                outcome = 'refunded'
            elif type(summary['amount_refunded']) is int and 0 < summary['amount_refunded'] < order['amount_minor']:
                db.execute("UPDATE payment_orders SET status='review_required',updated_at=? WHERE id=? AND status NOT IN ('refunded','disputed')", (now, order['id']))
                outcome = 'partial_refund_review_required'
        elif kind == 'charge.dispute.created' or summary.get('dispute_status') == 'lost':
            db.execute("UPDATE payment_orders SET status='disputed',updated_at=? WHERE id=? AND status!='refunded'", (now, order['id']))
            revoke_membership(db, order['id'], now)
            outcome = 'disputed'
        else:
            outcome = 'dispute_manual_review_required'
        _finish_event(db, event['event_id'], order['id'], now, outcome)
