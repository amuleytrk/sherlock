"""End-to-end RCA agent regression case — CPC-576 milestone-undo bug.

The original RCA (2026-04-23, documented in
`~/plans/work/designs/rca-tool/rcaAgentPrompt.md`) found that a milestone
undo "didn't update device_status" because the *middle of three calls* had
`milestone_type: "null"` (the *string*, not JSON null), which the API
treated as a normal forward, not an undo.

This test verifies the RCA agent reaches the same conclusion when given the
same bug report — the agent should surface "milestone_type" and "null" as
the cause in its `final-rca.md`.

**Requires:** real PPE creds (the agent calls Anthropic + queries MSSQL/
Cosmos). Marked `regression` and `live`; skipped by default. Run via:

    uv run pytest -m regression -v

If the original CPC-576 device data has aged out of PPE, replace TAPE_ID
with a substitute case from current PPE state and update EXPECTED_TERMS to
match the substitute's correct conclusion.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

import pytest


# ----------------------------------------------------------------------
# Configuration — UPDATE these to point at the live regression data.
# ----------------------------------------------------------------------
TAPE_ID = os.environ.get("CPC576_TAPE_ID", "REPLACE_WITH_CPC_576_TAPE_ID")
USER_REPORT = (
    f"In PPE, device {TAPE_ID} did a forward then an undo, but device_status "
    f"didn't change. The undo didn't take effect. Please figure out why."
)
EXPECTED_TERMS = ["milestone_type", "null"]
MAX_DURATION_SEC = 90
# ----------------------------------------------------------------------


@pytest.mark.regression
@pytest.mark.live
def test_rca_agent_reaches_correct_root_cause(tmp_path, monkeypatch):
    if "REPLACE_WITH" in TAPE_ID:
        pytest.skip(
            "CPC576_TAPE_ID env var not set — set it to a known-answer device "
            "in PPE (or update the constant in this file)"
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; this test requires live LLM access")

    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    monkeypatch.setenv("SHERLOCK_DEMO_MODE", "0")
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    from apps.api.agents.rca import run_rca

    async def collect():
        events = []
        async for evt in run_rca(USER_REPORT, entities={"tape_id": TAPE_ID, "env": "ppe"}):
            events.append(evt)
        return events

    start = time.monotonic()
    events = asyncio.run(collect())
    elapsed = time.monotonic() - start

    done_lines = [e for e in events if "rca_done" in e]
    assert done_lines, "agent did not emit rca_done"
    payload = re.search(r"data: ({.*})", done_lines[0]).group(1)
    rca_id = json.loads(payload)["rca_id"]
    final_rca = (tmp_path / rca_id / "final-rca.md").read_text(encoding="utf-8")

    assert elapsed < MAX_DURATION_SEC, f"RCA took {elapsed:.1f}s (max {MAX_DURATION_SEC}s)"
    for term in EXPECTED_TERMS:
        assert term.lower() in final_rca.lower(), (
            f"final-rca.md missing '{term}' — agent's actual conclusion was:\n\n{final_rca[:1500]}"
        )
