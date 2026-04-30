"""Sherlock FastAPI app — entry point for the web UI.

Routes:
    GET    /health                  — liveness probe + active env list
    GET    /envs                    — configured envs + per-tool availability
    POST   /chat                    — SSE-streamed agent trace for a user message
    GET    /sessions                — list past sessions (newest first, max 50)
    GET    /sessions/{id}           — fetch a single session + its messages
    DELETE /sessions/{id}           — cascade-delete a session + its RCA scratch dirs
    DELETE /sessions                — cascade-delete every session ("Clear all")
    GET    /rca/{rca_id}            — fetch a synthesized RCA artifact + analysis files
    GET    /rca/{rca_id}/audit      — fetch tool-call audit log for an RCA
    GET    /artifacts?path=...      — serve a file from the investigations dir (path-confined)
    GET    /briefings               — list scheduled / on-demand health briefings
    GET    /briefings/{id}          — fetch one briefing's full markdown
    POST   /briefings/run           — trigger a briefing on demand
    POST   /trace                   — SSE cross-service request trace by identifier

Lifecycle: when SHERLOCK_EPHEMERAL_SESSIONS=1, the FastAPI lifespan startup
wipes every session/message/audit row and every investigations/<rca_id>/
scratch dir, so each launch begins with a clean slate. Briefings have an
independent lifecycle and survive ephemeral wipes.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from apps.api import audit, demo, store
from apps.api.agents.discovery import run_discovery
from apps.api.agents.rca import run_rca
from apps.api.env_context import active_env, active_system
from apps.api.proactive.briefing import run_briefing
from apps.api.proactive.scheduler import get_scheduler
from apps.api.trace.runner import run_trace
from apps.api.router import classify
from apps.api.settings import get_settings
from apps.api.sse import sse


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup + shutdown lifecycle.

    On startup:
      - If SHERLOCK_EPHEMERAL_SESSIONS=1, wipe sessions + RCA scratch dirs.
      - If SHERLOCK_PROACTIVE_ENABLED=1, kick the briefing scheduler loop.

    On shutdown: stop the scheduler cleanly (best-effort)."""
    s = get_settings()
    if s.sherlock_ephemeral_sessions:
        summary = store.delete_all_sessions()
        print(
            f"[sherlock] ephemeral mode: wiped {summary['sessions_deleted']} sessions, "
            f"{summary['messages_deleted']} messages, {summary['audit_entries_deleted']} audit rows, "
            f"{summary['rca_ids_seen']} RCA scratch dirs (briefings preserved)"
        )

    scheduler = get_scheduler() if s.sherlock_proactive_enabled else None
    if scheduler is not None:
        scheduler.start()
        print(f"[sherlock] proactive scheduler started (interval={s.sherlock_briefing_interval_seconds}s)")

    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()


app = FastAPI(title="Sherlock", version="0.2.0", lifespan=_lifespan)


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
    # 'mssql' | 'postgres' — scopes RAG retrieval to one DB era. None = no
    # filter (legacy behavior, returns from both buckets).
    system: str | None = None


_VALID_SYSTEMS = {"mssql", "postgres"}


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

    # Active DB system filter for RAG retrieval. Defaults to mssql (current
    # production reality at Trackonomy); flip to postgres via the dropdown
    # once the migration ships.
    requested_system = (req.system or "mssql").lower()
    if requested_system not in _VALID_SYSTEMS:
        requested_system = "mssql"
    active_system.set(requested_system)

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

    yield sse(
        "session",
        {"id": session_id, "is_new": is_new, "env": requested_env, "system": requested_system},
    )

    answer_chunks: list[str] = []
    final_answer: str | None = None
    rca_id: str | None = None
    verification: dict | None = None

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
            elif name == "verification":
                # Discovery emits this AFTER the answer is fully streamed.
                if isinstance(data, dict):
                    verification = data
            yield chunk
    finally:
        # finally{} runs even if the client disconnects mid-stream, so
        # whatever the agent has produced so far still gets persisted.
        new_msg_id: int | None = None
        if rca_id:
            new_msg_id = store.append_message(
                session_id, role="agent", content="", rca_id=rca_id,
            )
        elif answer_chunks:
            new_msg_id = store.append_message(
                session_id, role="agent", content="".join(answer_chunks),
            )
        elif final_answer:
            new_msg_id = store.append_message(
                session_id, role="agent", content=final_answer,
            )

        # Persist the trust-layer verification alongside the message it
        # rated, so reloading a past session keeps the badge.
        if verification is not None and new_msg_id is not None:
            try:
                store.insert_claim_eval(
                    session_id=session_id, rca_id=rca_id,
                    message_id=new_msg_id,
                    aggregate_score=int(verification.get("score", 0)),
                    confidence_band=str(verification.get("band", "yellow")),
                    claims=verification.get("claims", []),
                )
            except Exception:
                # Persistence is best-effort; never fail the chat over it.
                pass


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


@app.delete("/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    """Cascade-delete a single session, its messages, audit rows, and any
    linked investigations/<rca_id>/ scratch dirs."""
    return store.delete_session(session_id)


@app.delete("/sessions")
def delete_all_sessions_endpoint():
    """Wipe every session, message, audit entry, and RCA scratch dir.
    Used by the "Clear all" UI button."""
    return store.delete_all_sessions()


# --- Briefings (proactive mode) ---


class BriefingRunRequest(BaseModel):
    env: str | None = None
    system: str | None = None


@app.get("/briefings")
def list_briefings_endpoint():
    """Most recent briefings, newest first."""
    return {"briefings": store.list_briefings(limit=30)}


@app.get("/briefings/{briefing_id}")
def get_briefing_endpoint(briefing_id: int):
    rec = store.get_briefing(briefing_id)
    if not rec:
        raise HTTPException(status_code=404, detail="briefing not found")
    return rec


@app.post("/briefings/run")
async def run_briefing_endpoint(req: BriefingRunRequest):
    """Trigger a briefing on demand. Honors the active env/system overrides."""
    s = get_settings()
    env = (req.env or s.sherlock_default_env).lower()
    if env not in s.configured_envs():
        raise HTTPException(status_code=400, detail=f"unknown env: {env}")
    system = (req.system or "mssql").lower()
    if system not in {"mssql", "postgres"}:
        raise HTTPException(status_code=400, detail=f"unknown system: {system}")
    active_env.set(env)
    active_system.set(system)
    return await run_briefing(triggered_by="manual")


# --- Cross-service trace ---


class TraceRequest(BaseModel):
    identifier: str
    env: str | None = None
    system: str | None = None
    since_seconds: int = 3600
    hint: str | None = None        # 'milestone' | 'device_event' | None (auto-detect)


@app.post("/trace")
async def trace_endpoint(req: TraceRequest):
    """SSE-streamed cross-service trace by identifier."""
    s = get_settings()
    env = (req.env or s.sherlock_default_env).lower()
    if env not in s.configured_envs():
        raise HTTPException(status_code=400, detail=f"unknown env: {env}")
    system = (req.system or "mssql").lower()
    active_env.set(env)
    active_system.set(system)
    cfg = s.env_config(env)

    async def stream():
        async for evt in run_trace(
            req.identifier.strip(),
            cfg=cfg,
            since_seconds=max(60, min(req.since_seconds, 86400)),
            hint=req.hint,
        ):
            yield evt

    return StreamingResponse(stream(), media_type="text/event-stream")


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
