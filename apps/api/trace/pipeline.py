"""Pipeline discovery: which services touch a given identifier?

Hybrid strategy:
1. Detect the identifier shape (qrcode, tape_id, correlation_id, EG event ID).
2. Apply known-flow shortcuts for canonical Trackonomy pipelines.
3. Fall back to corpus search ("which services log this kind of identifier?")
   to derive a service list dynamically.

The pipeline returned is an ORDERED list — the order matters for the
sequenceDiagram (left-to-right). For ambiguous cases we degrade gracefully,
fetching logs from a broad set of services and letting the stitcher figure
out which actually touched the request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Pipeline:
    identifier: str
    identifier_kind: str           # 'qrcode' | 'tape_id' | 'correlation_id' | 'event_id' | 'unknown'
    services: list[str]            # ordered, in flow direction
    flow_label: str                # human-readable name of the flow
    rationale: str                 # short explanation of why these services


# Trackonomy canonical flows. Each maps a (kind, hint) to an ordered service list.
# When a user pastes a qrcode, we prioritize the milestone flow; the device-event
# flow is the alternate. The actual matched service set is determined by which
# services had logs for this identifier — the pipeline list is just the
# *candidate* services to fetch from.
_MILESTONE_FLOW = ["external-service", "ingress-service", "device-management-service"]
_DEVICE_EVENT_FLOW = ["event-preprocessor-service", "ingress-service", "location-preprocessor"]
_BROAD_FLOW = [
    "external-service",
    "ingress-service",
    "event-preprocessor-service",
    "device-management-service",
    "ann-rule-engine",
]


_QRCODE = re.compile(r"^[0-9A-Z]{2,4}-[0-9]{4,8}-[0-9A-Z]{4,8}$", re.IGNORECASE)
_TAPE_ID = re.compile(r"^[0-9A-F]{12}$", re.IGNORECASE)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def detect_identifier(s: str) -> str:
    s = s.strip()
    if _QRCODE.match(s):
        return "qrcode"
    if _TAPE_ID.match(s):
        return "tape_id"
    if _UUID.match(s):
        return "correlation_id"
    return "unknown"


def discover_pipeline(identifier: str, *, hint: str | None = None) -> Pipeline:
    """Return the candidate service list for an identifier.

    `hint` overrides the flow when the user knows the context — e.g. "milestone"
    forces the milestone flow even for a tape_id (which would otherwise pick
    device-event). UI passes hint=None today; the field is reserved for
    future "Trace as: [milestone/device-event/...]" UX.
    """
    kind = detect_identifier(identifier)

    if hint == "milestone" or (hint is None and kind == "qrcode"):
        return Pipeline(
            identifier=identifier,
            identifier_kind=kind,
            services=_MILESTONE_FLOW,
            flow_label="Milestone (POST /external/messages → ingress → MSSQL)",
            rationale=(
                "qrcode-shaped identifier defaults to the milestone pipeline: "
                "external-service validates + builds payloads, ingress-service "
                "executes the lookup_parcels insert, device-management-service "
                "optionally updates device_status."
            ),
        )

    if hint == "device_event" or (hint is None and kind == "tape_id"):
        return Pipeline(
            identifier=identifier,
            identifier_kind=kind,
            services=_DEVICE_EVENT_FLOW + ["device-management-service"],
            flow_label="Device event (preprocessor → ingress → location)",
            rationale=(
                "tape_id-shaped identifier defaults to the device-event pipeline. "
                "If this is actually a milestone trace, pass hint='milestone' to "
                "switch flows."
            ),
        )

    # correlation_id / unknown: cast a wider net.
    return Pipeline(
        identifier=identifier,
        identifier_kind=kind,
        services=_BROAD_FLOW,
        flow_label="Broad service sweep",
        rationale=(
            "Identifier shape did not match a known flow. Fetching logs from "
            "the 5 most-trafficked services and letting the stitcher pick "
            "matches by content."
        ),
    )
