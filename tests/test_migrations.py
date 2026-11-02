import json
import sqlite3
from pathlib import Path

import pytest


def old_database(monkeypatch, tmp_path):
    path = tmp_path / 'legacy.db'
    monkeypatch.setenv('DATABASE_PATH', str(path))
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT,password_hash TEXT,created_at INTEGER,updated_at INTEGER);
            CREATE TABLE auth_sessions(id TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),created_at INTEGER,expires_at INTEGER,revoked_at INTEGER);
            CREATE TABLE ai_usage_requests(request_id TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),kind TEXT,quota_date TEXT,status TEXT,created_at INTEGER,expires_at INTEGER,completed_at INTEGER);
            CREATE TABLE summary_results(user_id INTEGER REFERENCES users(id),url TEXT,language TEXT,result_json TEXT,updated_at INTEGER,PRIMARY KEY(user_id,url,language));
            INSERT INTO users VALUES(7,'old@example.com','existing-password-hash',100,100);
            INSERT INTO auth_sessions VALUES('session-7',7,100,9999999999,NULL);
            INSERT INTO ai_usage_requests VALUES('request-7',7,'summary','2026-11-01','succeeded',100,200,150);
        ''')
        content = {'subtitle': {'has_subtitle': True, 'segments': [], 'full_text': '旧字幕'}, 'summary': '旧摘要', 'mindmap': '# 旧导图'}
        db.execute('INSERT INTO summary_results VALUES(?,?,?,?,?)', (7, 'https://example.com/old', 'zh', json.dumps(content), 150))
        before = {table: db.execute('SELECT * FROM ' + table).fetchall() for table in ('users', 'auth_sessions', 'ai_usage_requests', 'summary_results')}
    return path, before


def test_upgrade_keeps_old_accounts_usage_results_and_is_repeatable(monkeypatch, tmp_path):
    path, before = old_database(monkeypatch, tmp_path)
    from backend.database import get_summary_result, init_db

    init_db()
    saved = get_summary_result(7, 'https://example.com/old', 'zh')
    assert saved['summary'] == '旧摘要' and saved['result_id']
    init_db()
    assert get_summary_result(7, 'https://example.com/old', 'zh')['result_id'] == saved['result_id']
    with sqlite3.connect(path) as db:
        for table in ('users', 'auth_sessions', 'ai_usage_requests'):
            assert db.execute('SELECT * FROM ' + table).fetchall() == before[table]
        assert db.execute('SELECT user_id,url,language,result_json,updated_at FROM summary_results').fetchall() == before['summary_results']
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list((tmp_path / 'backups').glob('*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('SELECT * FROM summary_results').fetchall() == before['summary_results']


def test_upgrade_failure_rolls_back_schema_and_keeps_backup(monkeypatch, tmp_path):
    path, before = old_database(monkeypatch, tmp_path)
    # 模拟旧本机库里有同名但不兼容的表：索引建立失败应回滚整次升级。
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE download_history(id TEXT PRIMARY KEY)')
    from backend.database import init_db

    with pytest.raises(sqlite3.OperationalError):
        init_db()
    with sqlite3.connect(path) as db:
        assert 'result_id' not in [row[1] for row in db.execute('PRAGMA table_info(summary_results)')]
        assert db.execute('SELECT * FROM summary_results').fetchall() == before['summary_results']
        assert db.execute('SELECT * FROM users').fetchall() == before['users']
    assert list((tmp_path / 'backups').glob('*.db'))
