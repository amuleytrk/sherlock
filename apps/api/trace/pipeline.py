"""Pipeline discovery: which services touch a given identifier?

Hybrid strategy:
1. Detect the identifier shape (qrcode, tape_id, UUID, unknown).
2. Apply known-flow shortcuts for canonical Trackonomy PG-era pipelines.
3. Fall back to corpus search ("which services log this kind of identifier?")
   to derive a service list dynamically.

The pipeline returned is an ORDERED list — the order matters for the
sequenceDiagram (left-to-right). For ambiguous cases we degrade gracefully,
fetching logs from a broad set of services and letting the stitcher figure
out which actually touched the request.

UUID note: a UUID-shaped identifier is NOT necessarily a correlation_id.
  - Deterministic (MD5-derived) UUIDs are used for account_id and
    application_id — route those via device_event or broad sweep.
  - Request-scoped UUIDs attached by the gateway are true correlation_ids.
  When the shape is ambiguous, the broad sweep is the safe default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Pipeline:
    identifier: str
    identifier_kind: str           # 'qrcode' | 'tape_id' | 'uuid' | 'unknown'
    services: list[str]            # ordered, in flow direction
    flow_label: str                # human-readable name of the flow
    rationale: str                 # short explanation of why these services


# Trackonomy canonical PG-era flows. Each maps a (kind, hint) to an ordered
# service list. When a user pastes a qrcode, we prioritize the milestone flow;
# the device-event flow is the alternate. The actual matched service set is
# determined by which services had logs for this identifier — the pipeline list
# is just the *candidate* services to fetch from.
#
# Milestone flow:
#   external-service builds + validates pre-formed payload → ingress-service
#   executes insertMilestoneEvent into trk.device_event (PG) + Cosmos update →
#   device-management-service receives status back-publish via Event Grid.
#
# Device-event flow (standard 5264/5258):
#   event-preprocessor-service writes raw_device_event to PG + publishes to
#   Event Grid → ingress-service writes raw_device_event_info + publishes to
#   "Location Prioritization" EG topic → Azure Service Bus queue →
#   location-preprocessor performs device_event INSERT/UPDATE into trk.device_event.
#   device-management-service (rule engine) is downstream of location-preprocessor
#   via its own Event Grid topic.
_MILESTONE_FLOW = ["external-service", "ingress-service", "device-management-service"]
_DEVICE_EVENT_FLOW = [
    "event-preprocessor-service",   # raw_device_event INSERT (PG)
    "ingress-service",               # raw_device_event_info INSERT + EG → SvcBus
    "location-preprocessor",         # device_event INSERT/UPDATE (PG) — the writer
    "device-management-service",     # rule engine downstream via Event Grid
]
_BROAD_FLOW = [
    "external-service",
    "ingress-service",
    "event-preprocessor-service",
    "location-preprocessor",
    "device-management-service",
    "rule-engine",
    "health-service",
    "healthcare-service",
    "util-service",
    "messaging-service",
    "airline-service",
]


_QRCODE = re.compile(r"^[0-9A-Z]{2,4}-[0-9]{4,8}-[0-9A-Z]{4,8}$", re.IGNORECASE)
_TAPE_ID = re.compile(r"^[0-9A-F]{12}$", re.IGNORECASE)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def detect_identifier(s: str) -> str:
    """Return a coarse shape label for the identifier.

    UUID note: a UUID match is labelled ``uuid`` (not ``correlation_id``)
    because MD5-derived UUIDs are also used for ``account_id`` and
    ``application_id``.  Callers should not assume every UUID is a
    request-scoped correlation — check the hint or let the broad sweep
    disambiguate.
    """
    s = s.strip()
    if _QRCODE.match(s):
        return "qrcode"
    if _TAPE_ID.match(s):
        return "tape_id"
    if _UUID.match(s):
        return "uuid"
    return "unknown"


def discover_pipeline(identifier: str, *, hint: str | None = None) -> Pipeline:
    """Return the candidate service list for an identifier.

    `hint` overrides the flow when the user knows the context — e.g. "milestone"
    forces the milestone flow even for a tape_id (which would otherwise pick
    device-event). UI passes hint=None today; the field is reserved for
    future "Trace as: [milestone/device-event/...]" UX.

    UUID hint: pass hint='correlation_id' if the UUID is a known request
    correlation, or hint='device_event' if it is an account_id/application_id
    you want to trace through the device-event flow.  Without a hint, UUIDs
    fall through to the broad sweep.
    """
    kind = detect_identifier(identifier)

    if hint == "milestone" or (hint is None and kind == "qrcode"):
        return Pipeline(
            identifier=identifier,
            identifier_kind=kind,
            services=_MILESTONE_FLOW,
            flow_label="Milestone (POST /external/messages → ingress-service → PG device_event)",
            rationale=(
                "qrcode-shaped identifier defaults to the milestone pipeline: "
                "external-service validates + builds pre-formed payloads; "
                "ingress-service executes insertMilestoneEvent into trk.device_event (PG) "
                "and updates the Cosmos consumable doc synchronously (rolls back on "
                "Cosmos failure); device-management-service receives the "
                "update-device-status back-publish via Event Grid."
            ),
        )

    if hint == "device_event" or (hint is None and kind == "tape_id"):
        return Pipeline(
            identifier=identifier,
            identifier_kind=kind,
            services=_DEVICE_EVENT_FLOW,
            flow_label=(
                "Device event (event-preprocessor → ingress → SvcBus "
                "→ location-preprocessor → PG device_event)"
            ),
            rationale=(
                "tape_id (device_id) defaults to the device-event pipeline. "
                "event-preprocessor-service writes raw_device_event to PG and "
                "publishes to Event Grid. ingress-service resolves location priority "
                "and publishes to the Location Prioritization Event Grid topic, which "
                "feeds an Azure Service Bus queue. location-preprocessor consumes the "
                "queue and performs the device_event INSERT/UPDATE into trk.device_event. "
                "device-management-service (rule engine) is notified downstream via its "
                "own Event Grid topic. "
                "If this is a milestone trace, pass hint='milestone' to switch flows."
            ),
        )

    # UUID / unknown: UUID may be correlation_id OR account_id/application_id
    # (MD5-derived). Cast a wide net across all PG-era services.
    return Pipeline(
        identifier=identifier,
        identifier_kind=kind,
        services=_BROAD_FLOW,
        flow_label="Broad service sweep (PG-era)",
        rationale=(
            "Identifier shape did not match a specific flow. "
            "UUID-shaped identifiers may be a request correlation_id OR a "
            "deterministic account_id/application_id (MD5-derived). "
            "Fetching logs from all candidate services and letting the "
            "stitcher pick matches by content."
        ),
    )
