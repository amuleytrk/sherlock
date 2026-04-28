"""Demo mode — canned realistic agent responses keyed by query patterns.

When `SHERLOCK_DEMO_MODE=1`, the backend returns hand-crafted streamed traces
for a small set of "marquee" queries. This lets engineers (and judges) see the
full UI experience without setting up Anthropic / OpenAI / PPE credentials.

The canned data is deliberately realistic — it cites real Trackonomy file paths
and uses the actual schema names, but the values shown are static. Don't ship
demo mode to a production deployment.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import AsyncIterator

from apps.api.agents.scratch import Investigation
from apps.api.settings import get_settings
from apps.api.sse import sse


def _matches(message: str, *patterns: str) -> bool:
    msg = message.lower()
    return any(re.search(p.lower(), msg) for p in patterns)


def is_demo_query(message: str) -> str | None:
    """Return the canned scenario id if this query matches one, else None."""
    if _matches(message, r"\blabel.*white\b", r"\blabel.*tape\b", r"\bdevice labelling\b", r"\baddparcel\b"):
        return "discovery_label_white_tape"
    if _matches(message, r"cross_customer_mesh", r"cross customer mesh"):
        return "discovery_cross_customer_mesh_flag"
    if _matches(message, r"lime selection", r"lime.*algorithm", r"\bproxencoded\b"):
        return "discovery_lime_selection"
    if _matches(message, r"events.*not.*lookup_parcels", r"not appearing.*lookup", r"events not in lookup"):
        return "rca_events_not_in_lookup"
    # Note: rca_ingress_500 was advertised here but never had a distinct
    # canned scenario — the matcher was removed to avoid silently serving the
    # lookup_parcels RCA when a user typed an ingress-500 prompt. Add a real
    # scenario above this if you want a second RCA demo.
    return None


# ---- Discovery scenarios ----

_DISCOVERY_LABEL_WHITE_TAPE = {
    "citations": [
        {"file_path": "device-management-service/routes/devicesRoutes.js", "line_start": 24, "line_end": 38, "service": "device-management-service", "category": "api_route"},
        {"file_path": "device-management-service/controllers/DeviceController.js", "line_start": 102, "line_end": 187, "service": "device-management-service", "category": "controller"},
        {"file_path": "systemFlow.md", "line_start": 528, "line_end": 573, "service": "platform", "category": "architecture"},
        {"file_path": "device-management-service/CLAUDE.md", "line_start": 30, "line_end": 42, "service": "device-management-service", "category": "service_architecture"},
    ],
    "answer": (
        "Use **`POST /devices/v1/parcel`** in `device-management-service`.\n\n"
        "**Required headers:**\n"
        "- `customer_id`\n"
        "- `authorized_groups`\n\n"
        "**Body parameters:**\n"
        "- `tape_id` (string, 12-char hex MAC) — required\n"
        "- `qrcode` (string) — required\n"
        "- `appId` (string, e.g. `trk-white-parcel`) — required\n"
        "- `dstatus` (number, 1 = ACTIVATED) — required\n"
        "- `tt` (string, tape type — `white` for white tape) — required\n"
        "- `abc` (string, AssetBarCode) — optional but near-universal\n"
        "- `tdname` (string, display name) — optional\n\n"
        "**Behavior** (per [device-management-service/controllers/DeviceController.js:102-187]):\n"
        "1. Validates the request against `customer_cfg` for the `(customer_id, authorized_group, application_id)` triplet\n"
        "2. Inserts a row into `trk.tapecfg_db`\n"
        "3. Upserts a Cosmos document into the `consumables` container with partition key `[customer_id, authorized_group, application_id]` and id = `qrcode`\n"
        "4. If `dstatus=1`, publishes a health event to Event Grid topic `health-events` for `health-service` to pick up\n\n"
        "**Architecture context** (systemFlow.md:528-573): labelling is the *gate* — a device with no `tapecfg_db` row gets dropped at ingress with the `TPE7` (Tape Not Found) error code. Always label before sending data.\n\n"
        "Citations: [`device-management-service/routes/devicesRoutes.js:24-38`], [`device-management-service/controllers/DeviceController.js:102-187`], [`systemFlow.md:528-573`]."
    ),
}

_DISCOVERY_CROSS_CUSTOMER_MESH = {
    "citations": [
        {"file_path": "ingress-service/controllers/IngressController.js", "line_start": 376, "line_end": 658, "service": "ingress-service", "category": "controller"},
        {"file_path": "ingress-service/CLAUDE.md", "line_start": 78, "line_end": 110, "service": "ingress-service", "category": "service_architecture"},
        {"file_path": "systemFlow.md", "line_start": 38, "line_end": 38, "service": "platform", "category": "architecture"},
    ],
    "answer": (
        "**`feature_configuration.cross_customer_mesh_allowed`** is a per-customer-application boolean flag in `trk.customer_cfg.feature_configuration`. When `true`, devices belonging to *that* customer/application are allowed to be **scanned by infrastructure (gateways, plugs, milestones) belonging to a different customer** — and vice versa.\n\n"
        "**Default:** `false` (strict tenant isolation at the mesh layer).\n\n"
        "**Where it's read** (`ingress-service/controllers/IngressController.js:376-658`):\n"
        "- During `processMorePids` — when ingress receives a gateway scan, it cross-references each PID's owning customer\n"
        "- If `cross_customer_mesh_allowed=false` AND the scanned device's owner ≠ the gateway's owner → the event is dropped silently\n"
        "- If `true` → the event is processed normally and lookup_parcels gets a row\n\n"
        "**To look up the live value for a customer:**\n\n"
        "```\n"
        "trk_mssql_query(\n"
        "  query_type=\"feature_flags\",\n"
        "  params={\n"
        "    \"customer_id\": \"<customer>\",\n"
        "    \"authorized_group\": \"<group>\",\n"
        "    \"application_id\": \"<app>\"\n"
        "  }\n"
        ")\n"
        "```\n\n"
        "The result includes `cross_mesh` as one of the surfaced columns.\n\n"
        "Citations: [`ingress-service/controllers/IngressController.js:376-658`], [`ingress-service/CLAUDE.md:78-110`]."
    ),
}

_DISCOVERY_LIME_SELECTION = {
    "citations": [
        {"file_path": "ingress-service/helpers/limeSelection.js", "line_start": 12, "line_end": 134, "service": "ingress-service", "category": "helper"},
        {"file_path": "ingress-service/controllers/IngressController.js", "line_start": 376, "line_end": 658, "service": "ingress-service", "category": "controller"},
        {"file_path": "systemFlow.md", "line_start": 642, "line_end": 720, "service": "platform", "category": "architecture"},
    ],
    "answer": (
        "The lime selection algorithm lives in **`ingress-service/helpers/limeSelection.js:12-134`** and is invoked from `processMorePids` in `IngressController.js:376-658`.\n\n"
        "**What it does:** when a gateway sweep returns a list of nearby device PIDs (last 2 hex chars of MAC addresses), the algorithm picks the *single best lime* (milestone beacon) from the candidate set to attribute the scan to. The selection respects:\n\n"
        "- Redis cache `pidsToLimeIds:{facility_id}` (HGETALL) for the PID-to-lime-id map of the facility\n"
        "- Lime priority/zone metadata from `trk.zone` (loaded via `iDict:{tape_id}`)\n"
        "- A tiebreak on most-recent activation timestamp\n\n"
        "**Output:** the `chosen_lime` field that lands in `trk.proximity_db` and downstream `trk.lookup_parcels` rows.\n\n"
        "**Why it matters for debugging:** if a device shows up in `lookup_parcels` at the *wrong* facility, it's almost always a lime selection issue — usually a stale entry in `pidsToLimeIds` after a milestone was redeployed without expiring the Redis key.\n\n"
        "Citations: [`ingress-service/helpers/limeSelection.js:12-134`], [`systemFlow.md:642-720`]."
    ),
}

DISCOVERY_SCENARIOS = {
    "discovery_label_white_tape": _DISCOVERY_LABEL_WHITE_TAPE,
    "discovery_cross_customer_mesh_flag": _DISCOVERY_CROSS_CUSTOMER_MESH,
    "discovery_lime_selection": _DISCOVERY_LIME_SELECTION,
}


async def run_discovery_demo(scenario_id: str, message: str) -> AsyncIterator[str]:
    """Stream a canned discovery scenario."""
    scenario = DISCOVERY_SCENARIOS[scenario_id]

    yield sse("status", {"phase": "retrieving", "msg": "Searching corpus…"})
    await asyncio.sleep(0.3)

    yield sse(
        "status",
        {"phase": "retrieved", "msg": f"{len(scenario['citations'])} candidate chunks"},
    )
    yield sse("evidence", {"kind": "citation_list", "items": scenario["citations"]})
    await asyncio.sleep(0.2)

    yield sse("status", {"phase": "generating", "msg": "Composing grounded answer…"})
    await asyncio.sleep(0.4)

    # Stream the answer in realistic-sized chunks so the UI animates.
    answer = scenario["answer"]
    chunk_size = 32
    for i in range(0, len(answer), chunk_size):
        yield sse("answer_delta", {"text": answer[i : i + chunk_size]})
        await asyncio.sleep(0.04)

    yield sse("done", {})


# ---- RCA scenarios ----

_RCA_EVENTS_NOT_IN_LOOKUP = {
    "rca_id_prefix": "rca_demo_",
    "user_query_template": "Device AABBCCDDEEFF events not in lookup_parcels (PPE)",
    "evidence": [
        ("trk_kubectl_logs", "txt",
         "--- pod: ingress-service-7c9f8b-x4ksm ---\n"
         "2026-04-27T07:14:32.103Z ingress-service [INFO] Prox Request received correlation_id=abc-123-def-456 device=AABBCCDDEEFF customer=delta\n"
         "2026-04-27T07:14:32.118Z ingress-service [INFO] processOcc :: starting sequence dedupe correlation_id=abc-123-def-456\n"
         "2026-04-27T07:14:32.121Z ingress-service [ERROR] processOcc :: DEVICE_STATUS_INVALID device_status=-1 device=AABBCCDDEEFF\n"
         "2026-04-27T07:14:32.122Z ingress-service [WARN] aborting downstream publish — no lookup_parcels write\n"),
        ("trk_mssql_query", "json",
         json.dumps([{
             "tape_id": "AABBCCDDEEFF",
             "device_status": -1,
             "customer_id": "delta",
             "authorized_group": "cargo",
             "application_id": "trk-white-parcel",
             "tape_type": "white",
             "tape_personality": "Parcel",
             "facility": "JFK-T1",
             "activation_date": "2026-01-15T00:00:00Z",
             "lastupdate": 1746002500,
             "AssetBarCode": "DL12345678",
             "feature_configuration": "{\"lookup_event_insertion\": true, \"cross_customer_mesh_allowed\": false}",
             "application_name": "Delta Cargo White Parcel"
         }], indent=2)),
        ("sherlock_search", "json",
         json.dumps([{
             "file_path": "systemFlow.md",
             "line_start": 408, "line_end": 460,
             "service": "platform", "category": "architecture",
             "content": "## §4.9 device_status reference table\n\n| dstatus | meaning | ingress behavior |\n|---|---|---|\n| 1 | ACTIVATED | full processing, lookup_parcels insert, location enrichment, health events |\n| 0 | NOT_ACTIVATED | proximity_db only, no lookup_parcels |\n| -1 | REUSE_OR_EXPIRED | proximity_db only, no lookup_parcels |\n| -2 | LOST | logged but dropped |\n| 3 | REJECTED | dropped immediately |\n| 4 | ACTIVE_WITH_NO_ALERTS | proximity_db + lookup_parcels but no rule evaluation |\n| 5 | DEACTIVATED | dropped immediately |"
         }], indent=2)),
    ],
    "mermaid": "sequenceDiagram\n    participant QA as QA report\n    participant ING as ingress-service\n    participant DB as trk.tapecfg_db\n    participant LP as trk.lookup_parcels\n\n    QA->>ING: prox event for AABBCCDDEEFF\n    ING->>DB: SELECT device_status\n    DB-->>ING: device_status = -1\n    Note over ING: processOcc: DEVICE_STATUS_INVALID\n    ING->>ING: proximity-only path\n    ING-xLP: (no insert — silenced by status -1)\n",
    "final_rca": (
        "## Root Cause Analysis: Device AABBCCDDEEFF — events not appearing in lookup_parcels (PPE)\n\n"
        "### Summary\n"
        "Device `AABBCCDDEEFF` has `device_status = -1` (REUSE_OR_EXPIRED) in `trk.tapecfg_db`. Per `systemFlow.md §4.9`, this status causes ingress to take the **proximity-only path** — events land in `proximity_db` but `lookup_parcels` is intentionally skipped. This is *expected behavior* given the device's current state, not a code bug. Either the device was retired and is being reused, or it was incorrectly marked REUSE_OR_EXPIRED.\n\n"
        "### Timeline\n\n"
        "| Time (UTC) | Service | Event |\n"
        "|---|---|---|\n"
        "| 2026-04-27T07:14:32.103Z | ingress-service | Prox Request received, `correlation_id=abc-123-def-456` |\n"
        "| 2026-04-27T07:14:32.118Z | ingress-service | `processOcc` started sequence dedupe |\n"
        "| 2026-04-27T07:14:32.121Z | ingress-service | **ERROR: DEVICE_STATUS_INVALID** (`device_status=-1`) |\n"
        "| 2026-04-27T07:14:32.122Z | ingress-service | Aborted downstream publish; no `lookup_parcels` write |\n\n"
        "### Root Cause\n\n"
        "The device's `device_status` in `trk.tapecfg_db` is `-1` (REUSE_OR_EXPIRED). This status was last touched on `2026-04-27T05:55:00Z` (lastupdate epoch `1746002500`). Per the platform architecture (`systemFlow.md §4.9`), `device_status=-1` puts the device on the **proximity-only** ingestion path — events are recorded in `trk.proximity_db` for telemetry retention but the `lookup_parcels` insert (which drives the dashboard's location view) is suppressed by design.\n\n"
        "The kubectl pod log confirms the path: `ingress-service` calls `processOcc`, which checks `device_status` early via `iDict` cache (`iDict:AABBCCDDEEFF`) and aborts on the invalid-for-lookup branch. There is **no code bug** — the system is doing exactly what it was designed to do for status `-1` devices.\n\n"
        "Likely upstream cause: this device was probably marked REUSE_OR_EXPIRED by `airline-service` AWB-reuse detection (see `systemFlow.md §16.6`), or via the dashboard's manual deactivation flow. The `feature_configuration.lookup_event_insertion=true` flag is set, but it is overridden by the `device_status=-1` check before that flag is even consulted.\n\n"
        "### Classification\n\n"
        "- **Type:** Configuration / Operator State (not a code bug)\n"
        "- **Severity:** Medium (single device, but blocks customer-facing tracking)\n"
        "- **Scope:** Single device (AABBCCDDEEFF)\n\n"
        "### Evidence\n\n"
        "- `evidence/001-trk_kubectl_logs.txt` — `processOcc :: DEVICE_STATUS_INVALID` error in ingress-service pod\n"
        "- `evidence/002-trk_mssql_query.json` — `device_status=-1`, `lastupdate=1746002500`\n"
        "- `evidence/003-sherlock_search.json` — `systemFlow.md §4.9` device_status reference\n"
        "- `analysis/service-hops.mmd` — sequence diagram of the proximity-only path\n\n"
        "### Remediation\n\n"
        "**If this device should be active** (the customer expects to see it on the dashboard):\n\n"
        "Re-label via `device-management-service`:\n\n"
        "```bash\n"
        "curl -X POST https://api.trackonomy.com/devices/v1/parcel \\\n"
        "  -H 'customer_id: delta' \\\n"
        "  -H 'authorized_groups: cargo' \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\n"
        "    \"tape_id\": \"AABBCCDDEEFF\",\n"
        "    \"qrcode\": \"<original-qr>\",\n"
        "    \"appId\": \"trk-white-parcel\",\n"
        "    \"dstatus\": 1,\n"
        "    \"tt\": \"white\"\n"
        "  }'\n"
        "```\n\n"
        "Then invalidate the iDict cache so ingress picks up the new status immediately:\n\n"
        "```\n"
        "redis-cli -u $REDIS_PPE_URL DEL iDict:AABBCCDDEEFF\n"
        "```\n\n"
        "**If this is expected** (the device was correctly retired): no action needed. The customer should not be expecting events on this AssetBarCode anymore.\n\n"
        "### Recommendation\n\n"
        "Reach out to the QA reporter to confirm whether they expected the device to still be active. If yes, walk through the re-labelling steps. If no, close as not-a-bug and add the example to the QA team's runbook.\n"
    ),
}


async def run_rca_demo(scenario_id: str, message: str, entities: dict | None = None) -> AsyncIterator[str]:
    """Stream a canned RCA scenario, including writing real evidence files
    into the scratch dir so the UI's RcaReport renders end-to-end."""
    scenarios = {
        "rca_events_not_in_lookup": _RCA_EVENTS_NOT_IN_LOOKUP,
    }
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        yield sse("status", {"phase": "demo", "msg": f"unknown demo RCA scenario: {scenario_id!r}"})
        yield sse("done", {})
        return

    s = get_settings()
    rca_id = scenario["rca_id_prefix"] + uuid.uuid4().hex[:6]
    inv = Investigation.create(
        root=s.sherlock_investigations_dir,
        rca_id=rca_id,
        user_query=message,
        entities=entities or {},
    )
    yield sse("rca_started", {"rca_id": inv.rca_id, "scratch_dir": str(inv.dir)})
    await asyncio.sleep(0.3)

    yield sse("agent_text", {
        "text": "Starting investigation. The smallest reliable identifier is the tape_id `AABBCCDDEEFF` in PPE. Let me check the kubectl pod logs for ingress-service first, then verify the device's row in `trk.tapecfg_db`."
    })
    await asyncio.sleep(0.4)

    # 1. kubectl logs
    yield sse("tool_call", {
        "id": "tu_demo_1",
        "name": "trk_kubectl_logs",
        "args": {"namespace": "trk", "label_selector": "app=ingress-service", "since_seconds": 600, "max_lines_per_pod": 40},
        "n": 1,
    })
    await asyncio.sleep(0.7)
    name1, ext1, content1 = scenario["evidence"][0]
    inv.write_evidence(name=name1, ext=ext1, content=content1)
    yield sse("tool_result", {"id": "tu_demo_1", "preview": content1[:500], "duration_ms": 312})
    yield sse("agent_text", {
        "text": "Found a `DEVICE_STATUS_INVALID` error in `ingress-service` for our device. Now I need to see what `device_status` is actually stored in `trk.tapecfg_db`."
    })
    await asyncio.sleep(0.4)

    # 2. MSSQL query
    yield sse("tool_call", {
        "id": "tu_demo_2",
        "name": "trk_mssql_query",
        "args": {"query_type": "device_config", "params": {"tape_id": "AABBCCDDEEFF"}},
        "n": 2,
    })
    await asyncio.sleep(0.6)
    name2, ext2, content2 = scenario["evidence"][1]
    inv.write_evidence(name=name2, ext=ext2, content=content2)
    yield sse("tool_result", {"id": "tu_demo_2", "preview": content2[:400], "duration_ms": 187})
    yield sse("agent_text", {
        "text": "**Confirmed:** `device_status = -1` (REUSE_OR_EXPIRED). Let me look up what the platform's reference says about this status to be sure of the behavior."
    })
    await asyncio.sleep(0.4)

    # 3. RAG lookup
    yield sse("tool_call", {
        "id": "tu_demo_3",
        "name": "sherlock_search",
        "args": {"query": "device_status -1 REUSE_OR_EXPIRED behavior", "category": "architecture", "top_k": 3},
        "n": 3,
    })
    await asyncio.sleep(0.5)
    name3, ext3, content3 = scenario["evidence"][2]
    inv.write_evidence(name=name3, ext=ext3, content=content3)
    yield sse("tool_result", {"id": "tu_demo_3", "preview": content3[:400], "duration_ms": 95})
    yield sse("agent_text", {
        "text": "Now I'll generate a service-hop diagram of the path the event took, then synthesize the RCA."
    })
    await asyncio.sleep(0.3)

    # 4. code_exec → mermaid
    yield sse("tool_call", {
        "id": "tu_demo_4",
        "name": "code_exec",
        "args": {"code": "open('/tmp/service-hops.mmd','w').write(MERMAID); print('wrote service-hops.mmd')"},
        "n": 4,
    })
    await asyncio.sleep(0.5)
    inv.write_analysis("service-hops.mmd", scenario["mermaid"])
    yield sse("tool_result", {
        "id": "tu_demo_4",
        "preview": "stdout:\nwrote service-hops.mmd\n\nstderr:\n\n\nproduced files:\nanalysis/service-hops.mmd",
        "duration_ms": 824,
    })
    await asyncio.sleep(0.3)

    # 5. write_final_rca
    yield sse("tool_call", {
        "id": "tu_demo_5",
        "name": "write_final_rca",
        "args": {"markdown": "<full RCA markdown — see final-rca.md>"},
        "n": 5,
    })
    await asyncio.sleep(0.4)
    inv.write_final_rca(scenario["final_rca"])
    yield sse("tool_result", {
        "id": "tu_demo_5",
        "preview": f"final-rca.md written ({len(scenario['final_rca'])} chars)",
        "duration_ms": 4,
    })

    yield sse("rca_done", {
        "rca_id": inv.rca_id,
        "scratch_dir": str(inv.dir),
        "final_rca_path": str(inv.dir / "final-rca.md"),
        "evidence_count": len(inv.list_evidence()),
        "analysis_count": len(inv.list_analysis()),
        "tool_calls": 5,
        "subagents": 0,
        "final_rca_written": True,
    })


# ---- entry points used by main.py ----


def is_active() -> bool:
    return get_settings().sherlock_demo_mode


async def run_demo(message: str, entities: dict | None = None) -> AsyncIterator[str]:
    """Pick the right scenario and yield its events."""
    scenario = is_demo_query(message)
    if scenario is None:
        # Not a recognized demo query — fall back to a "demo not configured" hint
        yield sse("status", {"phase": "demo", "msg": "demo mode is on but this query has no canned scenario"})
        yield sse("answer", {
            "text": (
                "Try one of: \n"
                "- *How do I label a white tape device?* (Discovery)\n"
                "- *What does feature_configuration.cross_customer_mesh_allowed do?* (Discovery)\n"
                "- *Where is the lime selection algorithm implemented?* (Discovery)\n"
                "- *Device AABBCCDDEEFF events not in lookup_parcels in PPE* (RCA)\n"
                "- *ingress-service is throwing 500 for customer Delta in PPE* (RCA)"
            )
        })
        yield sse("done", {})
        return

    if scenario.startswith("discovery_"):
        async for evt in run_discovery_demo(scenario, message):
            yield evt
    elif scenario.startswith("rca_"):
        async for evt in run_rca_demo(scenario, message, entities):
            yield evt
