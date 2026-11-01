"""账号和会话使用短连接；模型执行期间不持有数据库连接。"""

import os
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get_db_path() -> Path:
    path = Path(os.getenv("DATABASE_PATH", "backend/data/app.db"))
    return path if path.is_absolute() else ROOT / path


@contextmanager
def get_db():
    connection = sqlite3.connect(get_db_path(), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


def init_db():
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
            CREATE TABLE IF NOT EXISTS ai_usage_requests (
                request_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                kind TEXT NOT NULL CHECK(kind IN ('summary', 'chat')),
                quota_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved', 'succeeded', 'released')),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                completed_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_usage_user_day_kind
                ON ai_usage_requests(user_id, quota_date, kind, status);
            CREATE TABLE IF NOT EXISTS summary_results (
                user_id INTEGER NOT NULL REFERENCES users(id),
                url TEXT NOT NULL,
                language TEXT NOT NULL,
                result_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, url, language)
            );
        """)


def get_user_by_email(email: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str, password_hash: str) -> dict:
    now = int(time.time())
    with get_db() as db:
        cursor = db.execute("INSERT INTO users(email,password_hash,created_at,updated_at) VALUES(?,?,?,?)", (email, password_hash, now, now))
        return {"id": cursor.lastrowid, "email": email}


def create_session(user_id: int, expires_at: int) -> str:
    session_id = secrets.token_urlsafe(32)
    with get_db() as db:
        db.execute("INSERT INTO auth_sessions(id,user_id,created_at,expires_at) VALUES(?,?,?,?)", (session_id, user_id, int(time.time()), expires_at))
    return session_id


def get_session(session_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM auth_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def revoke_session(session_id: str):
    with get_db() as db:
        db.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (int(time.time()), session_id))


def get_summary_result(user_id: int, url: str, language: str) -> dict | None:
    with get_db() as db:
        row = db.execute('SELECT result_json,updated_at FROM summary_results WHERE user_id=? AND url=? AND language=?', (user_id, url, language)).fetchone()
        return {**json.loads(row['result_json']), 'saved_at': row['updated_at']} if row else None


def save_summary_result(db: sqlite3.Connection, user_id: int, result: dict, now: int):
    # 使用计次事务的连接，确保保存失败时成功次数也回滚。
    content = {name: result[name] for name in ('subtitle', 'summary', 'mindmap')}
    db.execute('''INSERT INTO summary_results(user_id,url,language,result_json,updated_at) VALUES(?,?,?,?,?)
        ON CONFLICT(user_id,url,language) DO UPDATE SET result_json=excluded.result_json,updated_at=excluded.updated_at''',
        (user_id, result['url'], result['language'], json.dumps(content, ensure_ascii=False), now))
