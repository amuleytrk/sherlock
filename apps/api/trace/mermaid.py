"""Mermaid sequenceDiagram generator for stitched traces."""
from __future__ import annotations

import re

from apps.api.trace.stitcher import StitchedTrace, TraceEvent


_SAFE = re.compile(r"[^A-Za-z0-9 \-_:./()]")


def _mermaid_safe(text: str, max_len: int = 100) -> str:
    """Strip characters that break Mermaid parsing and clip length."""
    s = _SAFE.sub(" ", text)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _participant_alias(service: str) -> str:
    """Stable short alias usable as a Mermaid participant identifier."""
    parts = service.replace("_", "-").split("-")
    short = "".join(p[:3].upper() for p in parts if p)[:6]
    return short or "SVC"


def render_mermaid(trace: StitchedTrace) -> str:
    """Render the trace as a Mermaid sequenceDiagram. Errors are highlighted
    via an `Note over` annotation and a thick-cross arrow style."""
    if not trace.events:
        # Still produce a valid (empty) diagram so the UI never errors.
        return (
            "sequenceDiagram\n"
            f"    participant U as User\n"
            f"    Note over U: No events found for `{_mermaid_safe(trace.identifier)}`\n"
        )

    aliases: dict[str, str] = {}
    for svc in trace.services_seen:
        if svc not in aliases:
            base = _participant_alias(svc)
            alias = base
            n = 1
            # Avoid alias collisions.
            while alias in aliases.values():
                n += 1
                alias = f"{base}{n}"
            aliases[svc] = alias

    lines: list[str] = ["sequenceDiagram"]
    lines.append(f"    autonumber")
    for svc, alias in aliases.items():
        lines.append(f"    participant {alias} as {svc}")

    # Pair adjacent events from different services into hops.
    prev_event: TraceEvent | None = None
    last_arrow_from: str | None = None
    for i, e in enumerate(trace.events):
        a = aliases[e.service]
        msg = _mermaid_safe(e.message, max_len=80)
        ts_label = e.ts.strftime("%H:%M:%S.%f")[:-3] if e.ts else ""

        if prev_event is None or prev_event.service == e.service:
            # Self-note for events that don't represent a hop.
            note_kind = "Note right of" if last_arrow_from is None else "Note over"
            actor = a if last_arrow_from is None else last_arrow_from
            lines.append(f"    {note_kind} {actor}: [{ts_label}] {msg}")
        else:
            from_alias = aliases[prev_event.service]
            arrow = "-x" if e.is_error else "->>"
            lines.append(f"    {from_alias}{arrow}{a}: {msg}")
            last_arrow_from = a
        if e.is_error:
            lines.append(f"    Note over {a}: ⚠ {_mermaid_safe(e.message, max_len=120)}")
        prev_event = e

    return "\n".join(lines)
