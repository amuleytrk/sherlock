"""Cross-service log stitcher.

Takes raw kubectl-fetched log chunks per service, walks each line forward,
and assembles an ordered timeline of events that mention the user's
identifier or a correlation_id that propagated from earlier services.

Trackonomy services emit JSON-formatted log lines with a `correlation_id`
field — that's the key. As a request hops services, each service may
generate a NEW correlation_id but the previous one usually appears in a
"received" log. We capture every correlation_id that co-occurs with the
identifier, then expand the matched set on subsequent passes until it
stabilizes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from apps.api.trace.log_fetcher import ServiceLogs


_TS_PATTERN = re.compile(r"^(\S+)\s+(.*)$")  # kubectl --timestamps prefixes ISO ts + space
_CORRELATION_FIELDS = ("correlation_id", "correlationId", "correlation")


@dataclass
class TraceEvent:
    ts: datetime | None
    service: str
    correlation_id: str | None
    level: str
    message: str
    raw: str
    is_error: bool = False
    fields: dict = field(default_factory=dict)


@dataclass
class StitchedTrace:
    identifier: str
    events: list[TraceEvent]
    services_seen: list[str]
    correlation_ids: list[str]
    failure_event_idx: int | None = None  # index into events
    summary: str = ""


def _parse_timestamp(s: str) -> datetime | None:
    try:
        # kubectl emits RFC3339 with nanoseconds; truncate to microseconds.
        if "." in s:
            base, frac = s.split(".", 1)
            zsuffix = ""
            if frac.endswith("Z"):
                frac, zsuffix = frac[:-1], "Z"
            elif "+" in frac:
                idx = frac.index("+")
                frac, zsuffix = frac[:idx], frac[idx:]
            elif "-" in frac and len(frac) > 7:
                idx = frac.rfind("-")
                frac, zsuffix = frac[:idx], frac[idx:]
            frac = frac[:6]  # microseconds
            s = f"{base}.{frac}{zsuffix}"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _split_ts_and_line(line: str) -> tuple[datetime | None, str]:
    m = _TS_PATTERN.match(line.rstrip())
    if not m:
        return None, line.rstrip()
    return _parse_timestamp(m.group(1)), m.group(2)


def _try_json(line: str) -> dict | None:
    """Return parsed JSON if the line is a single JSON object, else None.
    Robust to the [pod/...] kubectl prefix being already stripped."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _is_error_line(level: str, msg: str) -> bool:
    if level.lower() in {"error", "fatal", "critical"}:
        return True
    return bool(re.search(r"\bError|exception|failed|✗|❌", msg, re.IGNORECASE))


def stitch(identifier: str, services_logs: Iterable[ServiceLogs]) -> StitchedTrace:
    """Build an ordered cross-service timeline of events related to `identifier`.

    Two-pass approach:
      Pass 1: collect every line that literally contains `identifier` and
              extract its correlation_ids.
      Pass 2: re-scan, also matching lines whose correlation_id is in the
              collected set — captures upstream/downstream hops that don't
              re-quote the identifier in every log line.
    """
    services_logs = list(services_logs)

    # Pass 1: find direct identifier matches + harvest correlation IDs.
    correlation_set: set[str] = set()
    seen_lines: list[tuple[str, str]] = []   # (service, raw_line)
    for sl in services_logs:
        if not sl.raw_log:
            continue
        for raw in sl.raw_log.splitlines():
            if identifier in raw:
                seen_lines.append((sl.service, raw))
                obj = _try_json(_split_ts_and_line(raw)[1])
                if obj:
                    for k in _CORRELATION_FIELDS:
                        v = obj.get(k)
                        if v and isinstance(v, str):
                            correlation_set.add(v)

    # Also grab Event Grid IDs that flow between services. They appear as
    # `"id":"<uuid>"` inside `sendToEventGrid :: event grid payload` lines.
    eg_id_pattern = re.compile(r'"id"\s*:\s*"([0-9a-f-]{36})"')
    for svc, raw in list(seen_lines):
        if "sendToEventGrid" in raw or "eventType" in raw:
            for m in eg_id_pattern.findall(raw):
                correlation_set.add(m)

    # Pass 2: scan all logs, include any line whose correlation_id is in the
    # collected set OR which contains an EG event ID we already track.
    events: list[TraceEvent] = []
    for sl in services_logs:
        if not sl.raw_log:
            continue
        for raw in sl.raw_log.splitlines():
            ts, body = _split_ts_and_line(raw)
            obj = _try_json(body) or {}
            corr = next((obj[k] for k in _CORRELATION_FIELDS if obj.get(k)), None)
            level = obj.get("level", "")
            msg = obj.get("message", "") or body[:280]

            matched = False
            if identifier in raw:
                matched = True
            elif corr and corr in correlation_set:
                matched = True
            elif any(eg in raw for eg in correlation_set if len(eg) == 36):
                matched = True

            if not matched:
                continue
            events.append(TraceEvent(
                ts=ts, service=sl.service, correlation_id=corr,
                level=level, message=str(msg)[:600],
                raw=raw, is_error=_is_error_line(level, str(msg)),
                fields={"app": obj.get("application_name")},
            ))

    # Order by timestamp; events with no parsed ts go last.
    events.sort(key=lambda e: (e.ts is None, e.ts or datetime.max))

    # Failure detection: first error-level event in the timeline.
    failure_idx = None
    for i, e in enumerate(events):
        if e.is_error:
            failure_idx = i
            break

    services_seen = []
    for e in events:
        if e.service not in services_seen:
            services_seen.append(e.service)

    if not events:
        summary = (
            f"No log lines mentioned `{identifier}` or a correlation that "
            f"propagated from it. Try a wider time window or check the env."
        )
    elif failure_idx is not None:
        f = events[failure_idx]
        summary = (
            f"Found {len(events)} events across {len(services_seen)} services. "
            f"First error: `{f.service}` — {f.message[:120]}"
        )
    else:
        summary = (
            f"Found {len(events)} events across {len(services_seen)} services. "
            f"No error-level events detected."
        )

    return StitchedTrace(
        identifier=identifier,
        events=events,
        services_seen=services_seen,
        correlation_ids=sorted(correlation_set),
        failure_event_idx=failure_idx,
        summary=summary,
    )
