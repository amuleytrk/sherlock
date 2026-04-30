"""End-to-end trace runner.

Stages:
1. discover  → identify the candidate service list from the identifier shape
2. fetch     → parallel kubectl logs across services (asyncio.gather)
3. stitch    → walk the logs, gather events that mention the identifier or a
               correlation_id that propagated from earlier services
4. render    → produce a Mermaid sequenceDiagram + a structured event list
5. summarize → optional Haiku call for a 2-3 sentence narrative

Exposed as an async generator yielding SSE strings, mirroring the discovery
and rca runners.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import AsyncIterator

from anthropic import Anthropic

from apps.api.env_context import EnvCreds
from apps.api.settings import get_settings
from apps.api.sse import sse
from apps.api.trace.log_fetcher import fetch_logs_parallel
from apps.api.trace.mermaid import render_mermaid
from apps.api.trace.pipeline import discover_pipeline
from apps.api.trace.stitcher import stitch


def _serialize_event(e) -> dict:
    return {
        "ts": e.ts.isoformat() if e.ts else None,
        "service": e.service,
        "correlation_id": e.correlation_id,
        "level": e.level,
        "message": e.message,
        "is_error": e.is_error,
    }


def _summarize_with_haiku(trace, identifier: str) -> str:
    """Optional 2-3 sentence narrative summary of the trace."""
    s = get_settings()
    if not s.anthropic_api_key or not trace.events:
        return ""
    client = Anthropic(api_key=s.anthropic_api_key)
    head = trace.events[:6]
    tail = trace.events[-3:] if len(trace.events) > 6 else []
    digest = "\n".join(
        f"- [{(e.ts.isoformat() if e.ts else '?')}] {e.service}: {e.message[:200]}"
        + (" (ERROR)" if e.is_error else "")
        for e in head + tail
    )
    msg = (
        f"You are Sherlock. A user pasted identifier `{identifier}` to trace its flow.\n\n"
        f"Stitched timeline ({len(trace.events)} events across {len(trace.services_seen)} services):\n"
        f"{digest}\n\n"
        f"In 2-3 sentences, narrate what the timeline shows. If there's an error, "
        f"call out which service emitted it and the likely cause. Plain text only."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=250,
            messages=[{"role": "user", "content": msg}],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        return f"(narrative skipped: {type(e).__name__})"


async def run_trace(
    identifier: str, *,
    cfg: EnvCreds,
    since_seconds: int = 3600,
    hint: str | None = None,
) -> AsyncIterator[str]:
    t0 = time.monotonic()

    yield sse("trace_started", {"identifier": identifier, "env": cfg.env})

    pipeline = discover_pipeline(identifier, hint=hint)
    yield sse("pipeline", {
        "kind": pipeline.identifier_kind,
        "flow": pipeline.flow_label,
        "services": pipeline.services,
        "rationale": pipeline.rationale,
    })

    yield sse("status", {"phase": "fetching", "msg": f"kubectl logs across {len(pipeline.services)} services in parallel…"})
    fetch_t0 = time.monotonic()
    services_logs = await fetch_logs_parallel(
        cfg, pipeline.services, since_seconds=since_seconds, max_lines_per_pod=2000,
    )
    fetch_ms = int((time.monotonic() - fetch_t0) * 1000)

    fetched = sum(1 for sl in services_logs if sl.raw_log)
    yield sse("logs_fetched", {
        "duration_ms": fetch_ms,
        "services_with_logs": fetched,
        "services_total": len(services_logs),
        "per_service": [
            {"service": sl.service, "pod_count": sl.pod_count,
             "bytes": len(sl.raw_log), "error": sl.error}
            for sl in services_logs
        ],
    })

    yield sse("status", {"phase": "stitching", "msg": f"matching `{identifier}` + propagated correlation_ids…"})
    trace = stitch(identifier, services_logs)
    yield sse("stitched", {
        "events": [_serialize_event(e) for e in trace.events],
        "services_seen": trace.services_seen,
        "correlation_ids": trace.correlation_ids,
        "failure_event_idx": trace.failure_event_idx,
        "summary": trace.summary,
    })

    mermaid = render_mermaid(trace)
    yield sse("mermaid", {"diagram": mermaid})

    if trace.events:
        yield sse("status", {"phase": "summarizing", "msg": "Composing narrative…"})
        narrative = _summarize_with_haiku(trace, identifier)
        if narrative:
            yield sse("narrative", {"text": narrative})

    total_ms = int((time.monotonic() - t0) * 1000)
    yield sse("trace_done", {
        "total_ms": total_ms,
        "fetch_ms": fetch_ms,
        "events": len(trace.events),
        "services": len(trace.services_seen),
        "failure_detected": trace.failure_event_idx is not None,
    })
