"""支付测试只使用临时 SQLite、测试签名和 mock Stripe，不访问真实商户。"""

import hashlib
import hmac
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def billing_setup(monkeypatch):
    import stripe

    from backend import billing, database
    from backend.api_billing import router
    from backend.auth import AuthenticatedUser, get_current_user

    for name, value in {
        'PAYMENT_MODE': 'test', 'PAYMENT_LIVE_ENABLED': 'false',
        'STRIPE_SECRET_KEY': 'sk_test_isolated', 'STRIPE_WEBHOOK_SECRET': 'whsec_isolated',
        'STRIPE_PRICE_ID_VIP_30D': 'price_isolated', 'FRONTEND_URL': 'http://localhost:5173',
    }.items():
        monkeypatch.setenv(name, value)
    database.init_db()
    user = database.create_user('billing@example.com', 'unused')
    other = database.create_user('other-billing@example.com', 'unused')
    sessions = {}
    create = Mock()

    def make_session(params, options):
        order_id = params['client_reference_id']
        session = {
            'id': 'cs_' + order_id, 'url': 'https://checkout.stripe.com/c/pay/' + order_id,
            'livemode': False, 'mode': 'payment', 'payment_status': 'paid',
            'amount_total': 990, 'currency': 'cny', 'payment_intent': 'pi_' + order_id,
            'client_reference_id': order_id, 'metadata': params['metadata'],
            'line_items': {'data': [{'price': {'id': 'price_isolated'}, 'quantity': 1}]},
        }
        sessions[session['id']] = session
        return stripe.checkout.Session.construct_from(session, 'sk_test_isolated')

    create.side_effect = make_session
    provider = SimpleNamespace(v1=SimpleNamespace(
        prices=SimpleNamespace(retrieve=Mock(return_value=stripe.Price.construct_from({
            'id': 'price_isolated', 'active': True, 'livemode': False,
            'type': 'one_time', 'unit_amount': 990, 'currency': 'cny',
        }, 'sk_test_isolated'))),
        checkout=SimpleNamespace(sessions=SimpleNamespace(
            create=create, retrieve=Mock(side_effect=lambda key, **kw: stripe.checkout.Session.construct_from(sessions[key], 'sk_test_isolated')),
        )),
    ))
    monkeypatch.setattr(billing, '_stripe_client', lambda settings: provider)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user['id'], user['email'], 'test')
    return SimpleNamespace(service=billing, database=database, user=user, other=other,
                           client=TestClient(app), create=create, provider=provider, sessions=sessions)


def signed(client, event, valid=True):
    payload = json.dumps(event, separators=(',', ':')).encode()
    stamp = int(time.time())
    digest = hmac.new(b'whsec_isolated', str(stamp).encode() + b'.' + payload, hashlib.sha256).hexdigest()
    signature = f't={stamp},v1={digest if valid else "0" * 64}'
    return client.post('/api/billing/webhook', content=payload, headers={'Stripe-Signature': signature})


def paid_event(session, event_id, paid_at=None):
    return {'id': event_id, 'type': 'checkout.session.completed', 'livemode': False,
            'created': paid_at or int(time.time()), 'data': {'object': session}}


def test_signed_payment_duplicate_concurrency_renewal_expiry_and_owner(billing_setup, monkeypatch):
    from backend.membership import get_entitlements

    env = billing_setup
    first = env.client.post('/api/billing/checkout', json={'request_key': 'first', 'plan_code': 'vip_30d'})
    assert first.status_code == 201
    assert env.client.post('/api/billing/checkout', json={'request_key': 'first', 'plan_code': 'vip_30d'}).json() == first.json()
    assert env.create.call_count == 1
    order_id = first.json()['data']['order_id']
    session = env.sessions['cs_' + order_id]
    paid_at = int(time.time())
    event = paid_event(session, 'evt_first', paid_at)
    assert signed(env.client, event, valid=False).status_code == 400
    assert get_entitlements(env.user['id'], paid_at)['tier'] == 'free'
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(response.status_code == 200 for response in pool.map(lambda _: signed(env.client, event), range(3)))
    entitlements = get_entitlements(env.user['id'], paid_at)
    assert entitlements['limits'] == {'summary': 30, 'chat': 100, 'translation': 20, 'transcription': 14400}
    assert entitlements['expires_at'] == paid_at + 30 * 86400
    assert env.service.get_order(env.user['id'], order_id)['status'] == 'paid'
    with pytest.raises(Exception) as absent:
        env.service.get_order(env.other['id'], order_id)
    assert absent.value.status_code == 404
    second = env.service.create_checkout(env.user['id'], 'second', 'vip_30d')
    event2 = paid_event(env.sessions['cs_' + second['order_id']], 'evt_second', paid_at + 10)

    def fail_grant(*args):
        raise sqlite3.OperationalError('isolated commit failure')

    with monkeypatch.context() as failure:
        failure.setattr(env.service, 'grant_membership', fail_grant)
        assert signed(env.client, event2).status_code == 503
    with env.database.get_db() as db:
        assert db.execute('SELECT 1 FROM payment_events WHERE event_id=?', ('evt_second',)).fetchone() is None
        assert db.execute('SELECT status FROM payment_orders WHERE id=?', (second['order_id'],)).fetchone()[0] == 'pending'
    assert signed(env.client, event2).status_code == 200
    assert get_entitlements(env.user['id'], paid_at + 10)['expires_at'] == paid_at + 60 * 86400
    assert get_entitlements(env.user['id'], paid_at + 60 * 86400)['tier'] == 'free'
    # 同一天会员到期只降低上限，已成功用量仍保留，不能再次领免费额度。
    from backend import quota, extension_quota
    expiry = paid_at + 60 * 86400
    with monkeypatch.context() as clock:
        clock.setattr(quota, 'time', lambda: expiry - 1)
        clock.setattr(extension_quota, 'time', lambda: expiry - 1)
        for index in range(4):
            quota.commit_usage(quota.reserve_usage(env.user['id'], 'summary').request_id)
            reservation = extension_quota.reserve_extension_usage(env.user['id'], 'translation', 1, f'expiry-{index}')
            with env.database.get_db() as db, db:
                db.execute('BEGIN IMMEDIATE')
                extension_quota.commit_extension_usage(reservation.request_id, db)
        before = quota.get_quota(env.user['id'])
        clock.setattr(quota, 'time', lambda: expiry)
        clock.setattr(extension_quota, 'time', lambda: expiry)
        after = quota.get_quota(env.user['id'])
        assert before['date'] == after['date']
        assert after['summary'] == {'limit': 3, 'used': 4, 'reserved': 0, 'remaining': 0}
        assert after['translation'] == {'limit': 3, 'used': 4, 'reserved': 0, 'remaining': 0, 'unit': 'requests'}
    with env.database.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM membership_grants').fetchone()[0] == 2
    refund = {'id': 'evt_recompute', 'type': 'charge.refunded', 'livemode': False,
              'created': paid_at + 20, 'data': {'object': {
                  'id': 'ch_recompute', 'payment_intent': session['payment_intent'],
                  'amount': 990, 'amount_refunded': 990, 'currency': 'cny', 'refunded': True,
              }}}
    assert signed(env.client, refund).status_code == 200
    assert get_entitlements(env.user['id'], paid_at + 20)['expires_at'] == paid_at + 10 + 30 * 86400


def test_refund_before_binding_replay_and_mismatches_never_grant(billing_setup, monkeypatch):
    from backend.membership import get_entitlements

    env = billing_setup
    checkout = env.service.create_checkout(env.user['id'], 'refund', 'vip_30d')
    order_id = checkout['order_id']
    session = env.sessions['cs_' + order_id]
    refund = {'id': 'evt_refund_early', 'type': 'charge.refunded', 'livemode': False,
              'created': int(time.time()), 'data': {'object': {
                  'id': 'ch_isolated', 'payment_intent': session['payment_intent'],
                  'amount': 990, 'amount_refunded': 990, 'currency': 'cny', 'refunded': True,
              }}}
    assert signed(env.client, refund).status_code == 200
    with env.database.get_db() as db:
        assert db.execute('SELECT status FROM payment_events WHERE event_id=?', ('evt_refund_early',)).fetchone()[0] == 'pending'
    assert signed(env.client, paid_event(session, 'evt_paid_late')).status_code == 200
    assert env.service.get_order(env.user['id'], order_id)['status'] == 'refunded'
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'free'
    assert signed(env.client, paid_event(session, 'evt_paid_again')).status_code == 200
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'free'
    bad = env.service.create_checkout(env.user['id'], 'mismatch', 'vip_30d')
    wrong = dict(env.sessions['cs_' + bad['order_id']], amount_total=1)
    assert signed(env.client, paid_event(wrong, 'evt_amount_wrong')).status_code == 200
    assert env.service.get_order(env.user['id'], bad['order_id'])['status'] != 'paid'
    good = env.service.create_checkout(env.user['id'], 'partial-refund', 'vip_30d')
    good_session = env.sessions['cs_' + good['order_id']]
    assert signed(env.client, paid_event(good_session, 'evt_good')).status_code == 200
    partial = {'id': 'evt_partial', 'type': 'charge.refunded', 'livemode': False,
               'created': int(time.time()), 'data': {'object': {
                   'id': 'ch_partial', 'payment_intent': good_session['payment_intent'],
                   'amount': 990, 'amount_refunded': 100, 'currency': 'cny', 'refunded': False,
               }}}
    assert signed(env.client, partial).status_code == 200
    assert env.service.get_order(env.user['id'], good['order_id'])['status'] == 'review_required'
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'vip'
    monkeypatch.setenv('PAYMENT_MODE', 'live')
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'free'
    assert env.client.get('/api/billing/plans').json()['data']['available'] is False
    monkeypatch.setenv('PAYMENT_MODE', 'test')
    dispute = {'id': 'evt_dispute', 'type': 'charge.dispute.created', 'livemode': False,
               'created': int(time.time()), 'data': {'object': {
                   'id': 'dp_isolated', 'payment_intent': good_session['payment_intent'],
                   'amount': 990, 'currency': 'cny', 'status': 'needs_response',
               }}}
    assert signed(env.client, dispute).status_code == 200
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'free'


def test_missing_config_price_and_network_timeout_retry_same_provider_key(billing_setup, monkeypatch):
    import stripe

    env = billing_setup
    monkeypatch.delenv('STRIPE_SECRET_KEY')
    assert env.client.get('/api/billing/plans').json()['data']['available'] is False
    assert env.client.post('/api/billing/checkout', json={'request_key': 'missing', 'plan_code': 'vip_30d'}).status_code == 503
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_isolated')
    for status in ('expired', 'async_payment_failed'):
        checkout = env.service.create_checkout(env.user['id'], status, 'vip_30d')
        session = env.sessions['cs_' + checkout['order_id']]
        event = {'id': 'evt_' + status, 'type': 'checkout.session.' + status,
                 'created': int(time.time()), 'livemode': False, 'data': {'object': session}}
        assert signed(env.client, event).status_code == 200
        assert env.service.get_order(env.user['id'], checkout['order_id'])['status'] == ('expired' if status == 'expired' else 'failed')
    from backend.membership import get_entitlements
    assert get_entitlements(env.user['id'], int(time.time()))['tier'] == 'free'
    original = env.create.side_effect
    env.create.side_effect = stripe.APIConnectionError('isolated timeout')
    request = {'request_key': 'timed-out', 'plan_code': 'vip_30d'}
    assert env.client.post('/api/billing/checkout', json=request).status_code == 503
    first_key = env.create.call_args.kwargs['options']['idempotency_key']
    with env.database.get_db() as db:
        assert db.execute('SELECT status FROM payment_orders WHERE request_key=?', ('timed-out',)).fetchone()[0] == 'creating'
    env.create.side_effect = original
    assert env.client.post('/api/billing/checkout', json=request).status_code == 201
    assert env.create.call_args.kwargs['options']['idempotency_key'] == first_key
    env.provider.v1.prices.retrieve.return_value['unit_amount'] = 1
    assert env.client.post('/api/billing/checkout', json={'request_key': 'bad-price', 'plan_code': 'vip_30d'}).status_code == 503
