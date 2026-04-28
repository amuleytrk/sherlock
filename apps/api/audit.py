"""Audit log helpers. Sanitizes args before persistence so credentials,
tokens, and other secrets never make it onto disk.
"""
from __future__ import annotations

import json
import re
import time

from apps.api import store


_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # key=value / key: value with optional quotes around either side.
    # Lookbehind `(?<![A-Za-z0-9])` allows underscore/dot/dash/start-of-string
    # before the keyword, so DATADOG_API_KEY, MSSQL_PPE_PASSWORD,
    # COSMOS_PPE_KEY, OPENAI_API_KEY, REDIS_PPE_KEY all match.
    (
        re.compile(
            r"""(?ix)
            (?<![A-Za-z0-9])
            (password|passwd|pwd|secret|token|api[-_]?key|access[-_]?key|bearer|key)
            ["']?\s*[:=]\s*
            ["']?[^"',}\s]+["']?
            """
        ),
        "***REDACTED***",
    ),
    # URL with embedded credentials: rediss://:pwd@host, postgresql://user:pwd@host.
    # Preserve scheme + user + @ so the audit log still shows the host shape.
    (
        re.compile(r"(://[^:/@\s]*:)([^@/\s]+)(@)"),
        r"\1***REDACTED***\3",
    ),
    # Plain "Bearer <token>" anywhere
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+"), "***REDACTED***"),
    # AWS-style access key ID
    (re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"), "***REDACTED***"),
    # JWT-shaped string anywhere
    (re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-\.]{20,}(?![A-Za-z0-9_\-])"), "***REDACTED***"),
    # OpenAI / similar prefixed keys
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "***REDACTED***"),
]


def redact(s: str) -> str:
    out = s
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def record(
    session_id: str | None,
    rca_id: str | None,
    tool_name: str,
    args: dict,
    outcome: str,
    duration_ms: int,
) -> None:
    raw = json.dumps(args, default=str)
    safe = redact(raw)
    with store.conn() as c:
        c.execute(
            "INSERT INTO audit_log "
            "(session_id, rca_id, tool_name, args_json, outcome, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, rca_id, tool_name, safe, outcome, duration_ms, store.now()),
        )


def list_for_rca(rca_id: str) -> list[dict]:
    with store.conn() as c:
        rows = c.execute(
            "SELECT tool_name, args_json, outcome, duration_ms, created_at "
            "FROM audit_log WHERE rca_id = ? ORDER BY id ASC",
            (rca_id,),
        ).fetchall()
        return [dict(r) for r in rows]


class TimedTool:
    """Context manager that times a tool call and records it to the audit log
    on exit. Records `outcome='error'` if an exception propagated."""

    def __init__(
        self,
        session_id: str | None,
        rca_id: str | None,
        tool_name: str,
        args: dict,
    ):
        self.session_id = session_id
        self.rca_id = rca_id
        self.tool_name = tool_name
        self.args = args
        self.start = 0.0

    def __enter__(self) -> "TimedTool":
        self.start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = int((time.monotonic() - self.start) * 1000)
        outcome = "error" if exc_type else "ok"
        try:
            record(
                self.session_id,
                self.rca_id,
                self.tool_name,
                self.args,
                outcome,
                duration_ms,
            )
        except Exception:
            # Audit log failure must never crash the actual tool call.
            pass
        return False  # do not suppress
