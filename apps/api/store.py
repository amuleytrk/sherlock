"""SQLite persistence for sessions, conversation history, and audit log.

Single SQLite file at `sherlock_db_path` from settings. Schema is
auto-created on first connection; safe to run repeatedly.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from apps.api.settings import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    rca_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    rca_id TEXT,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_rca ON audit_log(rca_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn():
    s = get_settings()
    db_path = Path(s.sherlock_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def list_sessions(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, title, created_at, updated_at FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_session(session_id: str, title: str | None = None) -> None:
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now(), session_id)
            )
        else:
            c.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, title or "Untitled", now(), now()),
            )


def append_message(
    session_id: str, role: str, content: str, rca_id: str | None = None
) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO messages (session_id, role, content, rca_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, rca_id, now()),
        )
