"""先做一致备份，再以短事务升级；升级失败保留原库并停止启动。"""

import secrets
import sqlite3
import time
from pathlib import Path

from .database import get_db, get_db_path


def backup_database(version: int | None = None) -> Path:
    directory = get_db_path().parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    marker = f'v{version}' if version is not None else 'manual'
    path = directory / f'backup-{marker}-{time.time_ns()}-{secrets.token_hex(3)}.db'
    with get_db() as source, sqlite3.connect(path) as target:
        source.backup(target)
    for old in sorted(directory.glob('backup-v*-*.db'), key=lambda item: item.stat().st_mtime_ns, reverse=True)[3:]:
        old.unlink()
    return path


def migrate_database():
    migrations = ((1, migrate_history), (2, migrate_tasks), (3, migrate_translations), (4, migrate_transcriptions), (5, migrate_billing))
    with get_db() as db:
        recorded = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        version = db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0] if recorded else 0
    if version == migrations[-1][0]:
        return
    if version > migrations[-1][0]:
        raise RuntimeError('数据库版本高于当前程序，请使用匹配的新版本，不要删除数据库')
    backup_database(migrations[-1][0])
    for number, apply in migrations:
        if number <= version:
            continue
        with get_db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL)')
            if db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0] >= number:
                continue
            apply(db)
            if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('数据库升级后的完整性检查未通过，已回滚；请保留数据库和备份')
            db.execute('INSERT INTO schema_migrations VALUES(?,?)', (number, int(time.time())))


def migrate_history(db):
    db.execute('ALTER TABLE summary_results ADD COLUMN result_id TEXT')
    db.execute("ALTER TABLE summary_results ADD COLUMN video_title TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE summary_results ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
    for row in db.execute('SELECT user_id,url,language FROM summary_results').fetchall():
        db.execute('UPDATE summary_results SET result_id=? WHERE user_id=? AND url=? AND language=?',
                   (secrets.token_urlsafe(24), row['user_id'], row['url'], row['language']))
    db.execute('CREATE UNIQUE INDEX idx_summary_result_id ON summary_results(result_id)')
    db.execute('CREATE INDEX idx_summary_history ON summary_results(user_id,updated_at DESC,result_id DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS download_history(
            id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),url TEXT NOT NULL,
            video_title TEXT NOT NULL,platform TEXT NOT NULL,format_id TEXT NOT NULL,status TEXT NOT NULL,
            origin TEXT NOT NULL,task_id TEXT,filename TEXT,error_code TEXT,
            created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)''')
    db.execute('CREATE INDEX idx_download_history ON download_history(user_id,updated_at DESC,id DESC)')


def migrate_tasks(db):
    db.execute('''CREATE TABLE task_batches(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        request_key TEXT NOT NULL,created_at INTEGER NOT NULL,deduplicated_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id,request_key))''')
    db.execute('''CREATE TABLE tasks(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        kind TEXT NOT NULL,batch_id TEXT REFERENCES task_batches(id),request_key TEXT NOT NULL,retry_of TEXT,
        input_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',stage TEXT NOT NULL DEFAULT 'queued',
        version INTEGER NOT NULL DEFAULT 1,progress_json TEXT NOT NULL DEFAULT '{}',error_code TEXT,error_message TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,started_at INTEGER,finished_at INTEGER,
        file_expires_at INTEGER,result_id TEXT,file_path TEXT,task_dir TEXT,filename TEXT,warning TEXT,
        UNIQUE(user_id,request_key))''')
    db.execute('CREATE INDEX idx_tasks_queue ON tasks(state,created_at,id)')
    db.execute('CREATE INDEX idx_tasks_user ON tasks(user_id,created_at DESC,id DESC)')
    db.execute('CREATE INDEX idx_tasks_batch ON tasks(user_id,batch_id)')


def migrate_translations(db):
    db.execute('ALTER TABLE tasks ADD COLUMN source_key TEXT')
    db.execute("CREATE UNIQUE INDEX idx_active_source ON tasks(user_id,kind,source_key) WHERE source_key IS NOT NULL AND state IN ('queued','running','cancelling')")
    db.execute('''CREATE TABLE extension_usage_requests(request_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL CHECK(kind IN ('translation','transcription')),
        quota_date TEXT NOT NULL,reserved_amount INTEGER NOT NULL,actual_amount INTEGER,
        status TEXT NOT NULL CHECK(status IN ('reserved','succeeded','released')),
        created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,completed_at INTEGER,task_id TEXT NOT NULL UNIQUE)''')
    db.execute('CREATE INDEX idx_extension_usage ON extension_usage_requests(user_id,quota_date,kind,status)')
    db.execute('''CREATE TABLE subtitle_translations(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        source_kind TEXT NOT NULL,source_id TEXT NOT NULL,source_hash TEXT NOT NULL,target_language TEXT NOT NULL,
        result_json TEXT NOT NULL,updated_at INTEGER NOT NULL,
        UNIQUE(user_id,source_kind,source_id,source_hash,target_language))''')


def migrate_transcriptions(db):
    db.execute('''CREATE TABLE transcription_results(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        url TEXT NOT NULL,result_json TEXT NOT NULL,audio_seconds INTEGER NOT NULL,model_name TEXT NOT NULL,
        updated_at INTEGER NOT NULL,UNIQUE(user_id,url))''')


def migrate_billing(db):
    db.execute('''CREATE TABLE payment_orders(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        request_key TEXT NOT NULL,plan_code TEXT NOT NULL,amount_minor INTEGER NOT NULL,currency TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,provider TEXT NOT NULL,payment_mode TEXT NOT NULL,
        checkout_session_id TEXT UNIQUE,payment_intent_id TEXT UNIQUE,status TEXT NOT NULL,
        created_at INTEGER NOT NULL,paid_at INTEGER,checkout_url TEXT,price_id TEXT NOT NULL,updated_at INTEGER NOT NULL,
        UNIQUE(user_id,request_key))''')
    db.execute('CREATE INDEX idx_orders_user ON payment_orders(user_id,created_at DESC,id DESC)')
    db.execute('''CREATE TABLE payment_events(event_id TEXT PRIMARY KEY,order_id TEXT REFERENCES payment_orders(id),
        event_type TEXT NOT NULL,payment_mode TEXT NOT NULL,provider_object_id TEXT,
        checkout_session_id TEXT,payment_intent_id TEXT,event_summary_json TEXT NOT NULL,status TEXT NOT NULL,
        received_at INTEGER NOT NULL,processed_at INTEGER,outcome TEXT)''')
    db.execute('CREATE INDEX idx_pending_payment_events ON payment_events(status,received_at,event_id)')
    db.execute('''CREATE TABLE membership_grants(order_id TEXT PRIMARY KEY REFERENCES payment_orders(id),
        user_id INTEGER NOT NULL REFERENCES users(id),payment_mode TEXT NOT NULL,paid_at INTEGER NOT NULL,
        duration_seconds INTEGER NOT NULL,revoked_at INTEGER)''')
    db.execute('CREATE INDEX idx_membership_user ON membership_grants(user_id,payment_mode,paid_at)')


if __name__ == '__main__':
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent/'.env')
    parser = argparse.ArgumentParser(description='备份当前项目SQLite，保留原库')
    parser.add_argument('command',choices=['backup'])
    parser.parse_args()
    print(backup_database())
