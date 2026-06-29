"""Demo mode — canned realistic agent responses keyed by query patterns.

When `SHERLOCK_DEMO_MODE=1`, the backend returns hand-crafted streamed traces
for a small set of "marquee" queries. This lets engineers (and judges) see the
full UI experience without setting up Anthropic / OpenAI / PPE credentials.

The canned data is deliberately realistic — it cites real Trackonomy file paths
and uses the actual PG schema names (trk.*), but the values shown are static.
Don't ship demo mode to a production deployment.

Schema notes (PPE PG):
- Tool: trk_postgres_query; engine: PostgreSQL 18, schema trk
- device.status is ENUM type_device_status (e.g. REUSE_OR_EXPIRED, ACTIVATED)
- device_event (was lookup_parcels) — written by location-preprocessor
- raw_device_event (was proximity_db) — written by event-preprocessor-service
- configuration WHERE type='FEATURE' holds feature flags per application
- No MSSQL tables (tapecfg_db / lookup_parcels / proximity_db / customer_cfg)
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
    # PG phrasing — device_event is the replacement for lookup_parcels.
    # Keep lookup_parcels as a legacy alias so existing bookmarked demo prompts
    # still work; both resolve to the same PG-based RCA scenario.
    if _matches(
        message,
        r"events.*not.*device_event",
        r"not.*appearing.*device_event",
        r"events.*not.*lookup_parcels",    # legacy alias
        r"not appearing.*lookup",          # legacy alias
        r"events not in lookup",           # legacy alias
    ):
        return "rca_events_not_in_lookup"
    if _matches(
        message,
        r"403.*out_of_chain",
        r"out_of_chain.*403",
        r"403.*v3.*device",
        r"empty.*device.*list.*v3",
        r"caruld.*device.*list",
        r"scope_violation.*authz",
        r"authz.*scope_violation",
        r"v3.*authz",
    ):
        return "rca_authz_403_out_of_chain"
    # Note: rca_ingress_500 was advertised here but never had a distinct
    # canned scenario — the matcher was removed to avoid silently serving the
    # device_event RCA when a user typed an ingress-500 prompt. Add a real
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
        "- `dstatus` (string ENUM, e.g. `ACTIVATED`) — required\n"
        "- `tt` (string, tape type — `white` for white tape) — required\n"
        "- `abc` (string, AssetBarCode) — optional but near-universal\n"
        "- `tdname` (string, display name) — optional\n\n"
        "**Behavior** (per [device-management-service/controllers/DeviceController.js:102-187]):\n"
        "1. Validates the request against `trk.account` + `trk.application` for the `(customer_id, authorized_group, application_id)` triplet\n"
        "2. Upserts a row into `trk.device` (PG), setting `status = 'ACTIVATED'` (ENUM)\n"
        "3. Upserts a Cosmos document into the `consumables` container with partition key `[customer_id, authorized_groups, application_id]` and id = `qrcode`\n"
        "4. If `dstatus=ACTIVATED`, publishes a health event to Event Grid topic `health-events` for `health-service` to pick up\n\n"
        "**Architecture context** (systemFlow.md:528-573): labelling is the *gate* — a device with no `trk.device` row (or `status='NOT_ACTIVATED'`) gets dropped at ingress. Always label before sending data.\n\n"
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
        "**`feature_configuration.cross_customer_mesh_allowed`** is a per-application boolean flag stored as JSONB in `trk.configuration` (WHERE `type='FEATURE'`). When `true`, devices belonging to *that* customer/application are allowed to be **scanned by infrastructure (gateways, plugs, milestones) belonging to a different customer** — and vice versa.\n\n"
        "**Default:** `false` (strict tenant isolation at the mesh layer).\n\n"
        "**Where it's read** (`ingress-service/controllers/IngressController.js:376-658`):\n"
        "- During `processMorePids` — when ingress receives a gateway scan, it cross-references each PID's owning account\n"
        "- If `cross_customer_mesh_allowed=false` AND the scanned device's owner ≠ the gateway's owner → the event is dropped silently; no `trk.device_event` row is written\n"
        "- If `true` → the event is processed normally and location-preprocessor writes a `device_event` row\n\n"
        "**To look up the live value for a customer (PPE):**\n\n"
        "```\n"
        "trk_postgres_query(\n"
        "  query_type=\"feature_flags\",\n"
        "  params={\n"
        "    \"application_id\": \"<application-uuid>\"\n"
        "  }\n"
        ")\n"
        "```\n\n"
        "The result includes `cross_mesh` as one of the surfaced columns (cast from JSONB). To resolve the application UUID, call `application_lookup` with `customer_id`, `authorized_group`, and `application_code` first.\n\n"
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
        "- Lime priority/zone metadata from `trk.zone` (loaded via `iDict:{device_id}`)\n"
        "- A tiebreak on most-recent activation timestamp\n\n"
        "**Output:** the `chosen_lime` field that lands in `trk.raw_device_event_info` and downstream `trk.device_event` rows (via location-preprocessor).\n\n"
        "**Why it matters for debugging:** if a device shows up in `trk.device_event` at the *wrong* facility, it's almost always a lime selection issue — usually a stale entry in `pidsToLimeIds` after a milestone was redeployed without expiring the Redis key.\n\n"
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
    "user_query_template": "Device AABBCCDDEEFF events not in device_event (PPE)",
    "evidence": [
        # Evidence 0: kubectl logs — UNAVAILABLE in PPE (DB-state-first RCA)
        ("trk_kubectl_logs", "txt",
         "--- kubectl logs: PPE pod logs UNAVAILABLE ---\n"
         "NOTE: AKS pod logs for PPE are not accessible from this context.\n"
         "Proceeding with DB-state-first RCA using trk_postgres_query.\n"
         "If logs become available, filter location-preprocessor for:\n"
         "  'Error inserting lookup parcel entry for tape: AABBCCDDEEFF'\n"
         "and ingress-service for correlation_id to trace the full hop.\n"),
        # Evidence 1: device_config PG query
        ("trk_postgres_query", "json",
         json.dumps([{
             "device_id": "AABBCCDDEEFF",
             "qrcode": "QR-AABBCCDDEEFF",
             "asset_barcode": "DL12345678",
             "device_status": "REUSE_OR_EXPIRED",
             "firmware": {"version": "2.4.1", "hw": "r3"},
             "battery_level": 72,
             "displayname": "Delta Cargo White Parcel",
             "facility_id": "fac-uuid-jfk-t1",
             "activation_date": "2026-01-15T00:00:00Z",
             "customer_id": "delta",
             "authorized_group": "cargo",
             "account_id": "acc-uuid-delta-cargo",
             "application_code": "trk-white-parcel",
             "application_id": "app-uuid-trk-white-parcel",
             "device_type": "WHITE",
             "asset_type": "CONSUMABLE",
             "classification": "PARCEL",
             "feature_configuration": {
                 "lookup_event_insertion": True,
                 "cross_customer_mesh_allowed": False,
                 "enableCellularEvents": True,
                 "limeMeshEvents": True
             }
         }], indent=2)),
        # Evidence 2: RAG lookup — systemFlow §4.9 ENUM status reference
        ("sherlock_search", "json",
         json.dumps([{
             "file_path": "systemFlow.md",
             "line_start": 408, "line_end": 460,
             "service": "platform", "category": "architecture",
             "content": (
                 "## §4.9 device status ENUM reference (PG: type_device_status)\n\n"
                 "| status (ENUM) | meaning | ingress / location-preprocessor behavior |\n"
                 "|---|---|---|\n"
                 "| ACTIVATED | Normal active device | full processing: device_event INSERT via location-preprocessor |\n"
                 "| FORCE_ACTIVATED | Force-activated override | same as ACTIVATED |\n"
                 "| ACTIVE_WITH_NO_ALERTS | Active, rule engine suppressed | device_event INSERT; no rule evaluation |\n"
                 "| NOT_ACTIVATED | Device not yet registered | raw_device_event only; no device_event |\n"
                 "| REUSE_OR_EXPIRED | Retired / AWB reuse | raw_device_event only; device_event INSERT suppressed |\n"
                 "| DEACTIVATED | Manually deactivated | dropped at ingress |\n"
                 "| REJECTED | Invalid device | dropped immediately |\n"
             )
         }], indent=2)),
    ],
    "mermaid": (
        "sequenceDiagram\n"
        "    participant QA as QA report\n"
        "    participant EP as event-preprocessor-service\n"
        "    participant ING as ingress-service\n"
        "    participant LP as location-preprocessor\n"
        "    participant DE as trk.device_event\n"
        "    participant RDE as trk.raw_device_event\n\n"
        "    QA->>EP: prox event for AABBCCDDEEFF\n"
        "    EP->>RDE: INSERT raw_device_event (always)\n"
        "    EP->>ING: publish to Event Grid (Prox/Ingress)\n"
        "    ING->>ING: read device from trk.device (iDict cache)\n"
        "    Note over ING: status='REUSE_OR_EXPIRED' — proximity-only path\n"
        "    ING->>LP: publish to Service Bus (PRIORITIZATION event)\n"
        "    LP->>LP: check device status gate\n"
        "    LP-xDE: (no INSERT — suppressed by REUSE_OR_EXPIRED)\n"
        "    Note over LP: 'Error inserting lookup parcel' NOT logged\\n(status gate fires before INSERT attempt)\n"
    ),
    "final_rca": (
        "## Root Cause Analysis: Device AABBCCDDEEFF — events not appearing in trk.device_event (PPE)\n\n"
        "### Summary\n"
        "Device `AABBCCDDEEFF` has `status = 'REUSE_OR_EXPIRED'` (ENUM `type_device_status`) in `trk.device` (PG). Per `systemFlow.md §4.9`, this ENUM value causes the ingress pipeline to take the **proximity-only path** — events are written to `trk.raw_device_event` by event-preprocessor-service, but `trk.device_event` (the PG replacement for `lookup_parcels`) is intentionally skipped by **location-preprocessor**. This is *expected behavior* given the device's current state, not a code bug. Either the device was retired and is being reused, or it was incorrectly marked REUSE_OR_EXPIRED.\n\n"
        "**Note on kubectl logs:** PPE pod logs are currently UNAVAILABLE from this context. The RCA above was derived entirely from DB state (`trk_postgres_query` with `query_type=device_config`). If logs become accessible, filter `location-preprocessor` (label `app=location-preprocessor-service`) for `'Error inserting lookup parcel entry for tape: AABBCCDDEEFF'` and `ingress-service` for the `correlation_id` to confirm the exact hop where suppression occurred.\n\n"
        "### Timeline\n\n"
        "| Step | Service | Action |\n"
        "|---|---|---|\n"
        "| 1 | event-preprocessor-service | Received prox event; wrote `trk.raw_device_event` row (always) |\n"
        "| 2 | event-preprocessor-service | Published to Event Grid (subject=`Prox/Ingress`) |\n"
        "| 3 | ingress-service | Read device from `trk.device` via iDict cache; resolved `status='REUSE_OR_EXPIRED'` |\n"
        "| 4 | ingress-service | Proximity-only path selected; published PRIORITIZATION event to Service Bus |\n"
        "| 5 | location-preprocessor | Consumed from Service Bus; status gate suppressed `device_event` INSERT |\n"
        "| — | trk.device_event | **No row written** (intended behavior for REUSE_OR_EXPIRED) |\n\n"
        "### Root Cause\n\n"
        "The device's `status` in `trk.device` is `'REUSE_OR_EXPIRED'` (PG ENUM `type_device_status`). Per `systemFlow.md §4.9`, this status puts the device on the **proximity-only** ingestion path. The `trk.device_event` INSERT — which drives the dashboard's location view — is suppressed by design in **location-preprocessor** (`LookupRepository.insertNonTriangulatedDevices` / `insertTriangulatedDevices` — the status gate fires before the INSERT attempt).\n\n"
        "The `feature_configuration.lookup_event_insertion=true` flag is set in `trk.configuration` (type='FEATURE'), but it is overridden by the `status='REUSE_OR_EXPIRED'` check which occurs before that flag is consulted.\n\n"
        "Likely upstream cause: this device was probably marked REUSE_OR_EXPIRED by `airline-service` AWB-reuse detection (see `systemFlow.md §16.6`), or via the dashboard's manual deactivation flow.\n\n"
        "### Classification\n\n"
        "- **Type:** Configuration / Operator State (not a code bug)\n"
        "- **Severity:** Medium (single device, but blocks customer-facing tracking)\n"
        "- **Scope:** Single device (AABBCCDDEEFF)\n\n"
        "### Evidence\n\n"
        "- `evidence/001-trk_kubectl_logs.txt` — PPE logs UNAVAILABLE; DB-state-first RCA path used\n"
        "- `evidence/002-trk_postgres_query.json` — `status='REUSE_OR_EXPIRED'`, `feature_configuration` JSONB (query_type=device_config)\n"
        "- `evidence/003-sherlock_search.json` — `systemFlow.md §4.9` ENUM status reference\n"
        "- `analysis/service-hops.mmd` — sequence diagram of the proximity-only path (PG pipeline)\n\n"
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
        "    \"qrcode\": \"QR-AABBCCDDEEFF\",\n"
        "    \"appId\": \"trk-white-parcel\",\n"
        "    \"dstatus\": \"ACTIVATED\",\n"
        "    \"tt\": \"white\"\n"
        "  }'\n"
        "```\n\n"
        "Then invalidate the iDict cache so ingress picks up the new status immediately:\n\n"
        "```\n"
        "redis-cli -u $REDIS_PPE_URL DEL iDict:AABBCCDDEEFF\n"
        "```\n\n"
        "**If this is expected** (the device was correctly retired): no action needed. The customer should not be expecting events on this AssetBarCode anymore.\n\n"
        "### Recommendation\n\n"
        "Reach out to the QA reporter to confirm whether they expected the device to still be active. If yes, walk through the re-labelling steps above (note: `dstatus` is now a string ENUM, not an integer). If no, close as not-a-bug and add the example to the QA team's runbook.\n"
    ),
}


# ---- v3 authz RCA scenario ----

_RCA_AUTHZ_403_OUT_OF_CHAIN = {
    "rca_id_prefix": "rca_demo_authz_",
    "user_query_template": "User caruld gets empty device list on GET /dash/v3/devices (PPE)",
    "evidence": [
        # Evidence 0: auth layer trace
        ("trk_kubectl_logs", "txt",
         "--- pod: api-gateway-v3-6f8d4b-r2pqt ---\n"
         "2026-06-28T09:11:42.003Z api-gateway [INFO] GET /dash/v3/devices user=caruld account=acc-uuid-caruld-org\n"
         "2026-06-28T09:11:42.008Z api-gateway [INFO] authz::resolveScope user=caruld requesting scope=account:acc-uuid-caruld-org\n"
         "2026-06-28T09:11:42.011Z api-gateway [WARN] authz::scopeCheck FAILED reason=out_of_chain user=caruld "
         "requested_account=acc-uuid-caruld-org ancestor_chain=[acc-uuid-root, acc-uuid-trk-global]\n"
         "2026-06-28T09:11:42.012Z api-gateway [INFO] authz::scopeCheck returning HTTP 403 scope_violation=out_of_chain\n"),
        # Evidence 1: account hierarchy query
        ("trk_postgres_query", "json",
         json.dumps([{
             "account_id": "acc-uuid-caruld-org",
             "customer_id": "caruld",
             "authorized_group": "fleet",
             "customer_name": "CarULD",
             "organization_id": "org-uuid-caruld",
             "parent_id": None,
             "metadata": {"tier": "L2", "provisioned_by": "ops-team", "authz_chain_set": False}
         }], indent=2)),
        # Evidence 2: RAG lookup — v3 authz model
        ("sherlock_search", "json",
         json.dumps([{
             "file_path": "systemFlow.md",
             "line_start": 801, "line_end": 845,
             "service": "platform", "category": "architecture",
             "content": (
                 "## §8.2 /v3 n-level authorization model\n\n"
                 "All /v3 endpoints enforce scope-chain authorization. A request is authorized\n"
                 "when the caller's `account_id` is either the target account itself, or a\n"
                 "direct ancestor in the `trk.account.parent_id` chain up to the root.\n\n"
                 "**scope_violation reasons:**\n"
                 "- `out_of_chain`: the requested account's parent_id chain does not include\n"
                 "  the caller's account_id. Most common cause: account was provisioned without\n"
                 "  setting parent_id, or was assigned to the wrong branch of the org tree.\n"
                 "- `missing_scope`: JWT claims do not include a scope for the target account.\n"
                 "- `expired_token`: token TTL exceeded.\n\n"
                 "Resolution for out_of_chain: set trk.account.parent_id to the correct\n"
                 "ancestor account UUID, then invalidate the authz cache for that account.\n"
             )
         }], indent=2)),
    ],
    "mermaid": (
        "sequenceDiagram\n"
        "    participant U as caruld (browser)\n"
        "    participant GW as api-gateway-v3\n"
        "    participant AZ as authz service\n"
        "    participant DB as trk.account (PG)\n\n"
        "    U->>GW: GET /dash/v3/devices\n"
        "    GW->>AZ: resolveScope(user=caruld, account=acc-uuid-caruld-org)\n"
        "    AZ->>DB: walk parent_id chain for acc-uuid-caruld-org\n"
        "    DB-->>AZ: parent_id = NULL (no chain)\n"
        "    AZ-->>GW: FAIL scope_violation=out_of_chain\n"
        "    GW-->>U: HTTP 403 {reason: out_of_chain}\n"
        "    Note over U: Dashboard shows empty device list\\n(403 silenced to empty in UI)\n"
    ),
    "final_rca": (
        "## Root Cause Analysis: caruld — empty device list on GET /dash/v3/devices (PPE)\n\n"
        "### Summary\n"
        "User `caruld` sees an empty device list on the `/dash/v3/devices` dashboard endpoint. The API gateway is returning HTTP 403 with `scope_violation=out_of_chain`, which the UI silences to an empty list. The root cause is that `trk.account.parent_id` for account `acc-uuid-caruld-org` is `NULL` — the account was provisioned without being placed in the org-tree ancestor chain required by the `/v3` n-level authorization model.\n\n"
        "### Timeline\n\n"
        "| Step | Component | Action |\n"
        "|---|---|---|\n"
        "| 1 | api-gateway-v3 | Received `GET /dash/v3/devices` for user `caruld` |\n"
        "| 2 | authz service | Called `resolveScope` for `acc-uuid-caruld-org` |\n"
        "| 3 | trk.account (PG) | `parent_id = NULL` — no ancestor chain found |\n"
        "| 4 | authz service | Returned `scope_violation=out_of_chain` |\n"
        "| 5 | api-gateway-v3 | Issued HTTP 403 |\n"
        "| 6 | UI | Silenced 403 → rendered empty device list |\n\n"
        "### Root Cause\n\n"
        "`trk.account.parent_id` for `acc-uuid-caruld-org` is `NULL`. Per `systemFlow.md §8.2`, the `/v3` authz model requires the requesting account to be the target account itself OR a node in the `parent_id` ancestor chain up to root. With `parent_id = NULL`, the chain walk terminates immediately and the `out_of_chain` check fires.\n\n"
        "This is an **account provisioning gap** — ops provisioned the account without setting its L2 parent in the org tree. The `metadata.authz_chain_set=false` flag on the account row confirms this was a known pending step.\n\n"
        "### Classification\n\n"
        "- **Type:** Provisioning / Configuration (not a code bug)\n"
        "- **Severity:** High (entire customer dashboard blocked)\n"
        "- **Scope:** Account `acc-uuid-caruld-org` (all users in that org)\n\n"
        "### Evidence\n\n"
        "- `evidence/001-trk_kubectl_logs.txt` — api-gateway-v3 `out_of_chain` 403 log line\n"
        "- `evidence/002-trk_postgres_query.json` — `parent_id=NULL`, `authz_chain_set=false` in account metadata (query_type=account_lookup)\n"
        "- `evidence/003-sherlock_search.json` — `systemFlow.md §8.2` v3 authz model + `out_of_chain` definition\n"
        "- `analysis/service-hops.mmd` — L2/L4 scope-chain sequence diagram\n\n"
        "### Remediation\n\n"
        "**Step 1 — Identify the correct parent account UUID:**\n\n"
        "```\n"
        "trk_postgres_query(\n"
        "  query_type=\"account_lookup\",\n"
        "  params={\"customer_id\": \"trk-global\", \"authorized_group\": \"root\"}\n"
        ")\n"
        "```\n\n"
        "**Step 2 — Set parent_id on the caruld account (ops action, requires write access):**\n\n"
        "```sql\n"
        "UPDATE trk.account\n"
        "  SET parent_id = '<correct-ancestor-uuid>',\n"
        "      metadata = metadata || '{\"authz_chain_set\": true}'\n"
        "WHERE id = 'acc-uuid-caruld-org';\n"
        "```\n\n"
        "**Step 3 — Invalidate the authz cache for the account:**\n\n"
        "```\n"
        "redis-cli -u $REDIS_PPE_URL DEL authz:acc-uuid-caruld-org\n"
        "```\n\n"
        "After the cache TTL expires (or explicit DEL), the next `GET /dash/v3/devices` request should resolve to HTTP 200 and populate the device list.\n\n"
        "### Recommendation\n\n"
        "Add a post-provisioning validation gate to the ops account-creation runbook: verify `parent_id IS NOT NULL` and `metadata->>'authz_chain_set' = 'true'` before handing off the account to the customer. Consider a DB constraint or a pre-flight check in the provisioning script.\n"
    ),
}


async def run_rca_demo(scenario_id: str, message: str, entities: dict | None = None) -> AsyncIterator[str]:
    """Stream a canned RCA scenario, including writing real evidence files
    into the scratch dir so the UI's RcaReport renders end-to-end."""
    scenarios = {
        "rca_events_not_in_lookup": _RCA_EVENTS_NOT_IN_LOOKUP,
        "rca_authz_403_out_of_chain": _RCA_AUTHZ_403_OUT_OF_CHAIN,
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

    if scenario_id == "rca_events_not_in_lookup":
        async for evt in _stream_rca_events_not_in_lookup(inv, scenario):
            yield evt
    elif scenario_id == "rca_authz_403_out_of_chain":
        async for evt in _stream_rca_authz_403(inv, scenario):
            yield evt


async def _stream_rca_events_not_in_lookup(inv: Investigation, scenario: dict) -> AsyncIterator[str]:
    """Inner stream for the device_event (REUSE_OR_EXPIRED) RCA."""
    yield sse("agent_text", {
        "text": (
            "Starting investigation. Device `AABBCCDDEEFF` in PPE — events not appearing in `trk.device_event`. "
            "PPE kubectl logs are currently UNAVAILABLE; proceeding with DB-state-first approach. "
            "Let me check the device config in `trk.device` via `trk_postgres_query` (query_type=device_config)."
        )
    })
    await asyncio.sleep(0.4)

    # 1. kubectl logs (note: UNAVAILABLE in PPE)
    yield sse("tool_call", {
        "id": "tu_demo_1",
        "name": "trk_kubectl_logs",
        "args": {"namespace": "trk", "label_selector": "app=location-preprocessor-service", "since_seconds": 600, "max_lines_per_pod": 40},
        "n": 1,
    })
    await asyncio.sleep(0.7)
    name1, ext1, content1 = scenario["evidence"][0]
    inv.write_evidence(name=name1, ext=ext1, content=content1)
    yield sse("tool_result", {"id": "tu_demo_1", "preview": content1[:500], "duration_ms": 218})
    yield sse("agent_text", {
        "text": (
            "Logs UNAVAILABLE in PPE. Switching to DB-state-first RCA. "
            "Querying `trk.device` with `trk_postgres_query` query_type=`device_config` for `AABBCCDDEEFF`."
        )
    })
    await asyncio.sleep(0.4)

    # 2. trk_postgres_query — device_config
    yield sse("tool_call", {
        "id": "tu_demo_2",
        "name": "trk_postgres_query",
        "args": {"query_type": "device_config", "params": {"device_id": "AABBCCDDEEFF"}},
        "n": 2,
    })
    await asyncio.sleep(0.6)
    name2, ext2, content2 = scenario["evidence"][1]
    inv.write_evidence(name=name2, ext=ext2, content=content2)
    yield sse("tool_result", {"id": "tu_demo_2", "preview": content2[:400], "duration_ms": 143})
    yield sse("agent_text", {
        "text": (
            "**Confirmed:** `status = 'REUSE_OR_EXPIRED'` (PG ENUM `type_device_status`). "
            "This ENUM value puts the device on the proximity-only path — `trk.device_event` INSERT is suppressed by location-preprocessor. "
            "Let me verify this against the platform architecture doc."
        )
    })
    await asyncio.sleep(0.4)

    # 3. RAG lookup
    yield sse("tool_call", {
        "id": "tu_demo_3",
        "name": "sherlock_search",
        "args": {"query": "REUSE_OR_EXPIRED device_event suppressed location-preprocessor status ENUM", "category": "architecture", "top_k": 3},
        "n": 3,
    })
    await asyncio.sleep(0.5)
    name3, ext3, content3 = scenario["evidence"][2]
    inv.write_evidence(name=name3, ext=ext3, content=content3)
    yield sse("tool_result", {"id": "tu_demo_3", "preview": content3[:400], "duration_ms": 88})
    yield sse("agent_text", {
        "text": "Architecture confirms: `REUSE_OR_EXPIRED` → proximity-only (raw_device_event only). Generating pipeline sequence diagram then synthesizing the RCA."
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
        "duration_ms": 731,
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


async def _stream_rca_authz_403(inv: Investigation, scenario: dict) -> AsyncIterator[str]:
    """Inner stream for the /v3 authz out_of_chain RCA."""
    yield sse("agent_text", {
        "text": (
            "Starting investigation. User `caruld` reports an empty device list on `/dash/v3/devices` in PPE. "
            "This pattern often means a silent 403 from the authz layer. "
            "Let me pull api-gateway-v3 pod logs first."
        )
    })
    await asyncio.sleep(0.4)

    # 1. kubectl logs — api-gateway-v3
    yield sse("tool_call", {
        "id": "tu_authz_1",
        "name": "trk_kubectl_logs",
        "args": {"namespace": "trk", "label_selector": "app=api-gateway-v3", "since_seconds": 300, "max_lines_per_pod": 30},
        "n": 1,
    })
    await asyncio.sleep(0.7)
    name1, ext1, content1 = scenario["evidence"][0]
    inv.write_evidence(name=name1, ext=ext1, content=content1)
    yield sse("tool_result", {"id": "tu_authz_1", "preview": content1[:500], "duration_ms": 267})
    yield sse("agent_text", {
        "text": (
            "Found it: `scope_violation=out_of_chain` for `caruld` on account `acc-uuid-caruld-org`. "
            "The `/v3` authz model walks the `parent_id` chain — `out_of_chain` means the walk found no valid ancestor. "
            "Let me check `trk.account` for that account."
        )
    })
    await asyncio.sleep(0.4)

    # 2. trk_postgres_query — account_lookup
    yield sse("tool_call", {
        "id": "tu_authz_2",
        "name": "trk_postgres_query",
        "args": {"query_type": "account_lookup", "params": {"customer_id": "caruld", "authorized_group": "fleet"}},
        "n": 2,
    })
    await asyncio.sleep(0.6)
    name2, ext2, content2 = scenario["evidence"][1]
    inv.write_evidence(name=name2, ext=ext2, content=content2)
    yield sse("tool_result", {"id": "tu_authz_2", "preview": content2[:400], "duration_ms": 121})
    yield sse("agent_text", {
        "text": (
            "**Confirmed:** `parent_id = NULL` and `metadata.authz_chain_set = false`. "
            "The account was provisioned without being placed in the org-tree. "
            "Looking up the authz model docs to confirm the remediation path."
        )
    })
    await asyncio.sleep(0.4)

    # 3. RAG lookup — v3 authz model
    yield sse("tool_call", {
        "id": "tu_authz_3",
        "name": "sherlock_search",
        "args": {"query": "v3 authz out_of_chain scope_violation parent_id ancestor chain", "category": "architecture", "top_k": 3},
        "n": 3,
    })
    await asyncio.sleep(0.5)
    name3, ext3, content3 = scenario["evidence"][2]
    inv.write_evidence(name=name3, ext=ext3, content=content3)
    yield sse("tool_result", {"id": "tu_authz_3", "preview": content3[:400], "duration_ms": 94})
    yield sse("agent_text", {
        "text": "Architecture doc confirms: `out_of_chain` = `parent_id` chain is broken. Fix is to SET `parent_id` to the correct ancestor UUID and invalidate the authz cache. Generating diagram then writing RCA."
    })
    await asyncio.sleep(0.3)

    # 4. code_exec → mermaid
    yield sse("tool_call", {
        "id": "tu_authz_4",
        "name": "code_exec",
        "args": {"code": "open('/tmp/service-hops.mmd','w').write(MERMAID); print('wrote service-hops.mmd')"},
        "n": 4,
    })
    await asyncio.sleep(0.5)
    inv.write_analysis("service-hops.mmd", scenario["mermaid"])
    yield sse("tool_result", {
        "id": "tu_authz_4",
        "preview": "stdout:\nwrote service-hops.mmd\n\nstderr:\n\n\nproduced files:\nanalysis/service-hops.mmd",
        "duration_ms": 698,
    })
    await asyncio.sleep(0.3)

    # 5. write_final_rca
    yield sse("tool_call", {
        "id": "tu_authz_5",
        "name": "write_final_rca",
        "args": {"markdown": "<full RCA markdown — see final-rca.md>"},
        "n": 5,
    })
    await asyncio.sleep(0.4)
    inv.write_final_rca(scenario["final_rca"])
    yield sse("tool_result", {
        "id": "tu_authz_5",
        "preview": f"final-rca.md written ({len(scenario['final_rca'])} chars)",
        "duration_ms": 3,
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
                "- *Device AABBCCDDEEFF events not in device_event in PPE* (RCA)\n"
                "- *User caruld sees empty device list on /dash/v3/devices in PPE* (RCA — v3 authz)\n"
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
