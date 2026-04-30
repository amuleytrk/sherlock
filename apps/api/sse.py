"""Server-Sent Events helpers.

Sherlock streams the agent's think-act-observe trace to the browser as a
sequence of SSE events. Each event has a name (the `event:` line) and a JSON
payload (the `data:` line). The frontend's ChatStream component dispatches
on the event name.

Event names emitted across the codebase:
    session        — session ID stamp (always first; tells client which session this turn belongs to)
    router         — routing decision, with extracted entities
    status         — phase transitions ("retrieving", "generating", etc.)
    evidence       — structured tool output for inline rendering (citation_list, etc.)
    tool_call      — agent invoked an MCP tool
    tool_result    — tool returned (preview + duration)
    agent_text     — agent's text block between tool calls
    answer_delta   — streamed answer token (Discovery)
    answer         — final answer (CONVERSATIONAL)
    rca_started    — RCA agent created a scratch dir
    rca_done       — RCA agent finished (with stats)
    done           — stream-end marker (frontend ignores; harmless)
"""
from __future__ import annotations

import json


def sse(event: str, data: dict) -> str:
    """Encode a single SSE message."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
