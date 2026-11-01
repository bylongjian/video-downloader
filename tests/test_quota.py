from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from backend import database


def account(email='quota@example.com'):
    database.init_db()
    return database.create_user(email,'unused-hash')["id"]


def test_quota_lifecycle_and_user_isolation():
    from backend import quota

    user=account()
    other=account('other@example.com')
    pending=quota.reserve_usage(user,'summary')
    snapshot=quota.get_quota(user)
    assert snapshot['summary']=={'limit':3,'used':0,'reserved':1,'remaining':2}
    assert snapshot['chat']['remaining']==10
    assert quota.get_quota(other)['summary']['remaining']==3
    completed=quota.commit_usage(pending.request_id)
    assert completed['summary']=={'limit':3,'used':1,'reserved':0,'remaining':2}
    assert quota.commit_usage(pending.request_id)['summary']['used']==1
    quota.release_usage(pending.request_id)
    question=quota.reserve_usage(user,'chat')
    quota.release_usage(question.request_id)
    assert quota.get_quota(user)['chat']['remaining']==10
    assert quota.get_quota(user)['summary']['used']==1


def test_last_slot_concurrent_reservations():
    from backend import quota

    user=account()
    for _ in range(2):
        quota.commit_usage(quota.reserve_usage(user,'summary').request_id)
    def reserve():
        try:
            return quota.reserve_usage(user,'summary')
        except quota.QuotaError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _:reserve(),range(2)))
    winners=[result for result in results if not isinstance(result,str)]
    assert len(winners)==1
    assert 'QUOTA_BUSY' in results
    quota.commit_usage(winners[0].request_id)
    with pytest.raises(quota.QuotaError,match='今日') as error:
        quota.reserve_usage(user,'summary')
    assert error.value.code=='QUOTA_EXHAUSTED'
    assert quota.get_quota(user)['summary']['used']==3


def test_midnight_and_expired_reservation(monkeypatch):
    from backend import quota

    user=account()
    midnight=int(datetime.fromisoformat('2026-11-02T00:00:00+08:00').timestamp())
    clock=[midnight-60]
    monkeypatch.setattr(quota,'time',lambda:clock[0])
    previous=quota.reserve_usage(user,'summary')
    clock[0]=midnight+60
    assert quota.get_quota(user)['summary']['remaining']==3
    assert quota.commit_usage(previous.request_id)['summary']['used']==0
    with database.get_db() as db:
        row=db.execute('SELECT quota_date,status FROM ai_usage_requests WHERE request_id=?',(previous.request_id,)).fetchone()
        assert tuple(row)==('2026-11-01','succeeded')
    stale=quota.reserve_usage(user,'chat')
    clock[0]=stale.expires_at
    quota.release_expired_usage()
    assert quota.get_quota(user)['chat']['remaining']==10
    with pytest.raises(quota.QuotaError):
        quota.commit_usage(stale.request_id)
    assert quota.get_quota(user)['chat']['used']==0


def test_maintenance_continues_after_temporary_database_lock(monkeypatch):
    import asyncio
    import sqlite3
    import threading
    from backend import main

    recovered=threading.Event()
    calls=[]
    def release():
        calls.append('quota')
        if len(calls)==2:
            raise sqlite3.OperationalError('database is locked')
        if len(calls)>2:
            recovered.set()
    original_sleep=asyncio.sleep
    async def next_round(seconds):
        await original_sleep(.01)
    monkeypatch.setattr(main,'release_expired_usage',release)
    monkeypatch.setattr(main.asyncio,'sleep',next_round)
    async def run():
        async with main.lifespan(main.app):
            assert await asyncio.to_thread(recovered.wait,2)
    asyncio.run(run())
