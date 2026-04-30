"""SQLite persistence for sessions, conversation history, and audit log.

Single SQLite file at `sherlock_db_path` from settings. Schema is
auto-created on first connection; safe to run repeatedly.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from apps.api.settings import get_settings


# rca_id format: `rca_<8 hex chars>` (see apps/api/agents/rca.py:_new_rca_id).
# Path validation gate before rmtree-ing any investigations/<rca_id>/ dir.
_RCA_ID_PATTERN = re.compile(r"^rca_[a-f0-9]{8}$")


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

-- Briefings: scheduled / on-demand health snapshots produced by the proactive
-- mode. Lifecycle is independent of chat sessions — they survive
-- SHERLOCK_EPHEMERAL_SESSIONS=1 startup wipes (operators want yesterday's
-- brief even after a server restart).
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    system TEXT NOT NULL,
    severity TEXT NOT NULL,        -- 'green' | 'yellow' | 'red'
    title TEXT NOT NULL,
    summary TEXT NOT NULL,         -- short 1-line headline
    content_md TEXT NOT NULL,      -- full markdown brief
    probes_json TEXT NOT NULL,     -- per-probe results
    triggered_by TEXT NOT NULL,    -- 'cron' | 'manual' | 'watcher'
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_briefings_created ON briefings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_briefings_env ON briefings(env);

-- Claim evaluations: per-answer self-grading results from the verifier.
-- Not strictly required (we could re-run verification on demand), but caching
-- means revisiting an old answer doesn't re-burn Haiku tokens.
CREATE TABLE IF NOT EXISTS claim_evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    rca_id TEXT,
    message_id INTEGER,            -- FK to messages.id (nullable for ad-hoc)
    aggregate_score INTEGER NOT NULL,  -- 0-100
    confidence_band TEXT NOT NULL,     -- 'green' | 'yellow' | 'red'
    claims_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evals_message ON claim_evals(message_id);
CREATE INDEX IF NOT EXISTS idx_claim_evals_session ON claim_evals(session_id);
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
) -> int:
    """Append a message and return its rowid (used by the trust layer to
    attach verification rows to the just-saved message)."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO messages (session_id, role, content, rca_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, rca_id, now()),
        )
        return cur.lastrowid


def get_session(session_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None


def get_session_messages(session_id: str) -> list[dict]:
    """Messages for a session (oldest first) joined with their claim_evals
    so the UI can re-render the confidence badge on load."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, role, content, rca_id, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        msgs = [dict(r) for r in rows]
        if msgs:
            ids = tuple(m["id"] for m in msgs)
            ph = ",".join(["?"] * len(ids))
            ev_rows = c.execute(
                f"SELECT message_id, aggregate_score, confidence_band, claims_json "
                f"FROM claim_evals WHERE message_id IN ({ph})",
                ids,
            ).fetchall()
            evs = {}
            import json as _json
            for ev in ev_rows:
                try:
                    claims = _json.loads(ev["claims_json"])
                except Exception:
                    claims = []
                evs[ev["message_id"]] = {
                    "score": ev["aggregate_score"],
                    "band": ev["confidence_band"],
                    "claims": claims,
                }
            for m in msgs:
                m["verification"] = evs.get(m["id"])
        return msgs


def _rmtree_rca_dir(rca_id: str) -> None:
    """Remove an investigation's scratch dir, with strict path validation.

    The rca_id comes from the SQLite store, so it should match `rca_<hex8>`.
    Reject anything that doesn't, defending against path-traversal if the row
    ever gets corrupted (or future code paths inject differently-shaped IDs)."""
    if not _RCA_ID_PATTERN.match(rca_id):
        return
    s = get_settings()
    path = (s.sherlock_investigations_dir / rca_id).resolve()
    inv_root = s.sherlock_investigations_dir.resolve()
    try:
        path.relative_to(inv_root)
    except ValueError:
        return  # outside the investigations dir somehow — refuse
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def delete_session(session_id: str) -> dict:
    """Cascade-delete a single session: messages, audit_log, claim_evals,
    and RCA scratch dirs. Briefings are NOT touched (independent lifecycle).

    Returns counts so callers can confirm the wipe."""
    with conn() as c:
        rca_rows = c.execute(
            "SELECT DISTINCT rca_id FROM messages WHERE session_id = ? AND rca_id IS NOT NULL",
            (session_id,),
        ).fetchall()
        rca_ids = [r["rca_id"] for r in rca_rows if r["rca_id"]]

        audit_count = c.execute(
            "DELETE FROM audit_log WHERE session_id = ?", (session_id,)
        ).rowcount
        if rca_ids:
            placeholders = ",".join(["?"] * len(rca_ids))
            audit_count += c.execute(
                f"DELETE FROM audit_log WHERE rca_id IN ({placeholders})",
                rca_ids,
            ).rowcount
        c.execute("DELETE FROM claim_evals WHERE session_id = ?", (session_id,))
        msg_count = c.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,)
        ).rowcount
        sess_count = c.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        ).rowcount

    for rca_id in rca_ids:
        _rmtree_rca_dir(rca_id)

    return {
        "session_id": session_id,
        "sessions_deleted": sess_count,
        "messages_deleted": msg_count,
        "audit_entries_deleted": audit_count,
        "rca_dirs_deleted": len(rca_ids),
    }


# ---- Briefings (proactive mode) ----


def insert_briefing(
    *,
    env: str,
    system: str,
    severity: str,
    title: str,
    summary: str,
    content_md: str,
    probes: list[dict],
    triggered_by: str,
    duration_ms: int,
) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO briefings (env, system, severity, title, summary, content_md, "
            "probes_json, triggered_by, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (env, system, severity, title, summary, content_md,
             json_dumps_compact(probes), triggered_by, duration_ms, now()),
        )
        return cur.lastrowid


def list_briefings(limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, env, system, severity, title, summary, triggered_by, "
            "duration_ms, created_at FROM briefings ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_briefing(briefing_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM briefings WHERE id = ?", (briefing_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            import json as _json
            d["probes"] = _json.loads(d.pop("probes_json", "[]"))
        except Exception:
            d["probes"] = []
        return d


def delete_all_briefings() -> int:
    with conn() as c:
        return c.execute("DELETE FROM briefings").rowcount


# ---- Claim evaluations (trust layer) ----


def insert_claim_eval(
    *,
    session_id: str | None,
    rca_id: str | None,
    message_id: int | None,
    aggregate_score: int,
    confidence_band: str,
    claims: list[dict],
) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO claim_evals (session_id, rca_id, message_id, aggregate_score, "
            "confidence_band, claims_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, rca_id, message_id, aggregate_score, confidence_band,
             json_dumps_compact(claims), now()),
        )
        return cur.lastrowid


def latest_message_id_for_session(session_id: str, role: str = "agent") -> int | None:
    with conn() as c:
        row = c.execute(
            "SELECT id FROM messages WHERE session_id = ? AND role = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, role),
        ).fetchone()
        return row["id"] if row else None


def json_dumps_compact(obj) -> str:
    import json as _json
    return _json.dumps(obj, default=str, separators=(",", ":"))


def delete_all_sessions() -> dict:
    """Wipe every session, message, audit entry, claim eval — and every RCA
    scratch dir in `investigations/`. Used by the startup-flush hook (when
    SHERLOCK_EPHEMERAL_SESSIONS=1) and by the "Clear all" UI button.

    Briefings are NOT wiped (independent lifecycle — operators want yesterday's
    brief even after a server restart). The investigations/ root itself is
    preserved; only matching subdirs are removed."""
    with conn() as c:
        rca_rows = c.execute(
            "SELECT DISTINCT rca_id FROM messages WHERE rca_id IS NOT NULL"
        ).fetchall()
        rca_ids = [r["rca_id"] for r in rca_rows if r["rca_id"]]

        c.execute("DELETE FROM claim_evals")
        msg_count = c.execute("DELETE FROM messages").rowcount
        audit_count = c.execute("DELETE FROM audit_log").rowcount
        sess_count = c.execute("DELETE FROM sessions").rowcount

    # Also sweep the investigations/ dir — covers any orphaned scratch dirs
    # that may exist without DB rows (e.g. crashes during persist).
    s = get_settings()
    inv_root = s.sherlock_investigations_dir
    if inv_root.is_dir():
        for child in inv_root.iterdir():
            if child.is_dir() and _RCA_ID_PATTERN.match(child.name):
                shutil.rmtree(child, ignore_errors=True)

    return {
        "sessions_deleted": sess_count,
        "messages_deleted": msg_count,
        "audit_entries_deleted": audit_count,
        "rca_ids_seen": len(rca_ids),
    }
