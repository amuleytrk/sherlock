"""Sherlock FastAPI app — entry point for the web UI.

Routes mounted here:
    GET  /health          — liveness probe
    POST /chat            — SSE-streamed agent trace for a user message
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.api.settings import get_settings
from apps.api.sse import fake_trace_stream

app = FastAPI(title="Sherlock", version="0.1.0")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "release": s.sherlock_release}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/chat")
async def chat(req: ChatRequest):
    # Day-1 placeholder. Day-2 wires this to the router + Discovery agent;
    # Day-3 adds RCA dispatch.
    return StreamingResponse(fake_trace_stream(), media_type="text/event-stream")
