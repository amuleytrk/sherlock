"""Sherlock FastAPI app — entry point for the web UI.

Routes:
    GET  /health                  — liveness probe + active env list
    GET  /envs                    — configured envs + per-tool availability
    POST /chat                    — SSE-streamed agent trace for a user message
    GET  /sessions                — list past sessions (newest first, max 50)
    GET  /sessions/{id}           — fetch a single session + its messages
    GET  /rca/{rca_id}            — fetch a synthesized RCA artifact + analysis files
    GET  /rca/{rca_id}/audit      — fetch tool-call audit log for an RCA
    GET  /artifacts?path=...      — serve a file from the investigations dir (path-confined)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from apps.api import audit, demo, store
from apps.api.agents.discovery import run_discovery
from apps.api.agents.rca import run_rca
from apps.api.env_context import active_env
from apps.api.router import classify
from apps.api.settings import get_settings
from apps.api.sse import sse

app = FastAPI(title="Sherlock", version="0.1.0")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "release": s.sherlock_release,
        "demo_mode": s.sherlock_demo_mode,
        "envs": s.configured_envs(),
        "default_env": s.sherlock_default_env,
    }


@app.get("/envs")
def list_envs() -> dict:
    """Configured envs with per-tool availability flags.

    The frontend dropdown uses this to render the env list and grey out
    tools that aren't configured for a given env (so the user knows
    that e.g. switching to Stage means kubectl works but MSSQL doesn't yet)."""
    s = get_settings()
    return {
        "default": s.sherlock_default_env,
        "envs": [
            {"name": e, "availability": s.env_availability(e)}
            for e in s.configured_envs()
        ],
    }


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    env: str | None = None


async def _dispatch(req: ChatRequest):
    routed = classify(req.message)
    yield sse("router", {"intent": routed.intent, "entities": routed.entities})

    # Demo mode short-circuits to canned responses so the UI works even without
    # any external credentials. Set SHERLOCK_DEMO_MODE=1 in .env to enable.
    if demo.is_active():
        async for evt in demo.run_demo(req.message, entities=routed.entities):
            yield evt
        return

    if routed.intent == "API_DISCOVERY":
        async for evt in run_discovery(req.message):
            yield evt
        return

    if routed.intent == "DEBUGGING":
        async for evt in run_rca(req.message, entities=routed.entities):
            yield evt
        return

    # CONVERSATIONAL
    yield sse(
        "answer",
        {
            "text": (
                "Hi! Ask me about Trackonomy APIs, feature flags, code patterns, "
                "or describe a bug to investigate (e.g. 'device AABBCCDDEEFF events not in lookup_parcels')."
            )
        },
    )
    yield sse("done", {})


def _parse_sse_chunk(chunk: str) -> tuple[str, dict]:
    """Best-effort parse of an SSE chunk we just emitted, so the wrapper can
    sniff event names without changing the inner agents' yield contract."""
    parts = chunk.split("\n", 2)
    if (
        len(parts) < 2
        or not parts[0].startswith("event: ")
        or not parts[1].startswith("data: ")
    ):
        return "", {}
    try:
        return parts[0][len("event: "):], json.loads(parts[1][len("data: "):])
    except json.JSONDecodeError:
        return "", {}


async def _record_session(req: ChatRequest):
    """Persist user/agent turns and stamp the SSE stream with a `session` event.

    For new chats, mints a UUID and derives a title from the user's first message.
    For existing sessions, bumps `updated_at`. The agent's persisted reply is
    either the accumulated Discovery answer text, the Conversational answer, or
    a stub linked to an RCA's `rca_id` (the full RCA report is recoverable from
    /rca/{rca_id})."""
    s = get_settings()
    # Resolve and set active env BEFORE anything else — every MCP server reads
    # active_env.get() to pick the right kubeconfig, MSSQL creds, etc.
    requested_env = (req.env or s.sherlock_default_env).lower()
    if requested_env not in s.configured_envs():
        requested_env = s.sherlock_default_env.lower()
    active_env.set(requested_env)

    session_id = req.session_id
    is_new = not session_id
    if is_new:
        session_id = str(uuid.uuid4())
        title = req.message.strip()[:60]
        if len(req.message.strip()) > 60:
            title += "..."
        store.upsert_session(session_id, title=title or "Untitled")
    else:
        store.upsert_session(session_id)
    store.append_message(session_id, role="user", content=req.message)

    yield sse("session", {"id": session_id, "is_new": is_new, "env": requested_env})

    answer_chunks: list[str] = []
    final_answer: str | None = None
    rca_id: str | None = None

    try:
        async for chunk in _dispatch(req):
            name, data = _parse_sse_chunk(chunk)
            if name == "answer_delta":
                t = data.get("text")
                if isinstance(t, str):
                    answer_chunks.append(t)
            elif name == "answer":
                t = data.get("text")
                if isinstance(t, str):
                    final_answer = t
            elif name == "rca_done":
                r = data.get("rca_id")
                if isinstance(r, str):
                    rca_id = r
            yield chunk
    finally:
        # finally{} runs even if the client disconnects mid-stream, so
        # whatever the agent has produced so far still gets persisted.
        if rca_id:
            # The full RCA report is recoverable via /rca/{rca_id}; no body needed.
            store.append_message(session_id, role="agent", content="", rca_id=rca_id)
        elif answer_chunks:
            store.append_message(session_id, role="agent", content="".join(answer_chunks))
        elif final_answer:
            store.append_message(session_id, role="agent", content=final_answer)


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(_record_session(req), media_type="text/event-stream")


@app.get("/sessions")
def list_sessions_endpoint():
    """Return the last 50 sessions, newest first."""
    return {"sessions": store.list_sessions(limit=50)}


@app.get("/sessions/{session_id}")
def get_session_endpoint(session_id: str):
    """Return a session's metadata + ordered messages (oldest first)."""
    sess = store.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    return {**sess, "messages": store.get_session_messages(session_id)}


def _resolve_artifact_path(path: str) -> Path:
    """Resolve a user-supplied path against the investigations dir, refusing
    paths that escape the root (path-traversal guard).

    Absolute paths are rejected outright — clients should always pass relative
    paths returned by /rca/{rca_id} (which are always relative to the
    investigations dir).
    """
    if Path(path).is_absolute():
        raise HTTPException(status_code=400, detail="absolute paths are not accepted")
    s = get_settings()
    root = s.sherlock_investigations_dir.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=403, detail="path out of scope") from e
    return target


@app.get("/artifacts")
def get_artifact(path: str):
    target = _resolve_artifact_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)


@app.get("/rca/{rca_id}")
def get_rca(rca_id: str):
    s = get_settings()
    inv_root = s.sherlock_investigations_dir.resolve()
    rca_dir = (inv_root / rca_id).resolve()
    try:
        rca_dir.relative_to(inv_root)
    except ValueError as e:
        raise HTTPException(status_code=403, detail="rca_id out of scope") from e
    if not rca_dir.is_dir():
        raise HTTPException(status_code=404, detail="rca not found")

    final = rca_dir / "final-rca.md"
    meta = rca_dir / "meta.json"
    analysis_dir = rca_dir / "analysis"
    evidence_dir = rca_dir / "evidence"

    analysis_files = sorted(analysis_dir.glob("*")) if analysis_dir.exists() else []
    evidence_files = sorted(evidence_dir.glob("*")) if evidence_dir.exists() else []

    return {
        "rca_id": rca_id,
        "meta": json.loads(meta.read_text()) if meta.exists() else {},
        "final_rca_markdown": final.read_text(encoding="utf-8") if final.exists() else None,
        "analysis_files": [
            {"name": p.name, "path": str(p.relative_to(inv_root))} for p in analysis_files
        ],
        "evidence_files": [
            {"name": p.name, "path": str(p.relative_to(inv_root))} for p in evidence_files
        ],
    }


@app.get("/rca/{rca_id}/audit")
def get_audit_endpoint(rca_id: str):
    """Tool-call audit log for an RCA, oldest first."""
    return {"entries": audit.list_for_rca(rca_id)}
