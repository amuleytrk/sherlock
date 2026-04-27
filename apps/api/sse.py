"""Server-Sent Events helpers.

Sherlock streams the agent's think-act-observe trace to the browser as a
sequence of SSE events. Each event has a name (the `event:` line) and a JSON
payload (the `data:` line). The frontend's ChatStream component dispatches
on the event name.
"""
from __future__ import annotations

import json
from typing import AsyncIterator


def sse(event: str, data: dict) -> str:
    """Encode a single SSE message."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def fake_trace_stream() -> AsyncIterator[str]:
    """Yield a fake agent trace for Day-1 smoke testing without a real LLM call."""
    import asyncio

    yield sse("status", {"phase": "routing", "msg": "Classifying intent…"})
    await asyncio.sleep(0.4)
    yield sse("status", {"phase": "investigating", "msg": "Tailing pod logs…"})
    await asyncio.sleep(0.6)
    yield sse(
        "evidence",
        {
            "kind": "log",
            "lines": [
                "2026-04-26T22:00:01Z ingress-service [INFO] Prox Request received",
                "2026-04-26T22:00:01Z ingress-service [ERROR] DEVICE_STATUS_INVALID",
            ],
        },
    )
    await asyncio.sleep(0.4)
    yield sse(
        "answer",
        {"text": "This is a placeholder answer from the Day-1 SSE smoke test."},
    )
    yield sse("done", {})
