"""Sherlock FastAPI app — entry point for the web UI.

Routes mounted here:
    GET  /health          — liveness probe
    POST /chat            — SSE-streamed agent trace for a user message
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
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

    if routed.intent == "API_DISCOVERY":
        async for evt in run_discovery(req.message):
            yield evt
        return

    if routed.intent == "DEBUGGING":
        # RCA agent lands Day 3 — until then, surface a clear message.
        try:
            from apps.api.agents.rca import run_rca
        except ImportError:
            yield sse("status", {"phase": "rca-not-yet", "msg": "RCA agent ships Day 3."})
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
