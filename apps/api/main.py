"""Sherlock FastAPI app — entry point for the web UI.

Routes mounted here:
    GET  /health                  — liveness probe
    POST /chat                    — SSE-streamed agent trace for a user message
    GET  /sessions                — list past sessions (Day 5 wires real persistence)
    GET  /rca/{rca_id}            — fetch a synthesized RCA artifact + analysis files
    GET  /rca/{rca_id}/audit      — fetch tool-call audit log for an RCA
    GET  /artifacts?path=...      — serve a file from the investigations dir (path-confined)
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from apps.api.agents.discovery import run_discovery
from apps.api.router import classify
from apps.api.settings import get_settings
from apps.api.sse import sse

app = FastAPI(title="Sherlock", version="0.1.0")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "release": s.sherlock_release}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


async def _dispatch(req: ChatRequest):
    routed = classify(req.message)
    yield sse("router", {"intent": routed.intent, "entities": routed.entities})

    # Demo mode short-circuits to canned responses so the UI works even without
    # any external credentials. Set SHERLOCK_DEMO_MODE=1 in .env to enable.
    from apps.api import demo
    if demo.is_active():
        async for evt in demo.run_demo(req.message, entities=routed.entities):
            yield evt
        return

    if routed.intent == "API_DISCOVERY":
        async for evt in run_discovery(req.message):
            yield evt
        return

    if routed.intent == "DEBUGGING":
        try:
            from apps.api.agents.rca import run_rca
        except ImportError:
            yield sse("status", {"phase": "rca-not-yet", "msg": "RCA agent module not available."})
            yield sse("done", {})
            return
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


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(_dispatch(req), media_type="text/event-stream")


@app.get("/sessions")
def list_sessions_endpoint():
    """Return the last 50 sessions. Day 5 wires the SQLite store; for now we
    return an empty list so the sidebar renders cleanly without errors."""
    try:
        from apps.api.store import list_sessions  # noqa: F401  — Day 5
        return {"sessions": list_sessions(limit=50)}
    except ImportError:
        return {"sessions": []}


def _resolve_artifact_path(path: str) -> Path:
    """Resolve a user-supplied path against the investigations dir, refusing
    paths that escape the root (path-traversal guard)."""
    s = get_settings()
    root = s.sherlock_investigations_dir.resolve()
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
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
    """Audit log for an RCA. Day 5 wires the SQLite store."""
    try:
        from apps.api.audit import list_for_rca  # noqa: F401  — Day 5
        return {"entries": list_for_rca(rca_id)}
    except ImportError:
        return {"entries": []}
