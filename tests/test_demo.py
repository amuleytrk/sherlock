"""Tests for demo-mode scenario routing + canned response correctness."""
from __future__ import annotations

import asyncio

from apps.api import demo


def test_demo_label_white_tape_matches():
    assert demo.is_demo_query("How do I label a white tape device?") == "discovery_label_white_tape"
    assert demo.is_demo_query("How do I label a new white tape?") == "discovery_label_white_tape"


def test_demo_cross_customer_mesh_matches():
    assert demo.is_demo_query("What does feature_configuration.cross_customer_mesh_allowed do?") == "discovery_cross_customer_mesh_flag"
    assert demo.is_demo_query("Tell me about cross customer mesh") == "discovery_cross_customer_mesh_flag"


def test_demo_lime_selection_matches():
    assert demo.is_demo_query("Where is the lime selection algorithm implemented?") == "discovery_lime_selection"
    assert demo.is_demo_query("How does proxencoded work?") == "discovery_lime_selection"


def test_demo_rca_events_not_in_device_event_matches():
    """PG phrasing — device_event is the canonical table name."""
    assert demo.is_demo_query("Device AABB events not in device_event in PPE") == "rca_events_not_in_lookup"
    assert demo.is_demo_query("events not appearing in device_event") == "rca_events_not_in_lookup"


def test_demo_rca_events_lookup_parcels_legacy_alias_matches():
    """lookup_parcels should still route to the same PG-based RCA scenario."""
    assert demo.is_demo_query("Device AABB events not in lookup_parcels in PPE") == "rca_events_not_in_lookup"
    assert demo.is_demo_query("not appearing in lookup_parcels") == "rca_events_not_in_lookup"
    assert demo.is_demo_query("events not in lookup_parcels") == "rca_events_not_in_lookup"


def test_demo_rca_authz_403_out_of_chain_matches():
    assert demo.is_demo_query("User gets 403 out_of_chain on v3 devices endpoint") == "rca_authz_403_out_of_chain"
    assert demo.is_demo_query("caruld sees empty device list on /dash/v3/devices") == "rca_authz_403_out_of_chain"
    assert demo.is_demo_query("scope_violation authz issue in PPE") == "rca_authz_403_out_of_chain"
    assert demo.is_demo_query("403 out_of_chain for v3 device search") == "rca_authz_403_out_of_chain"


def test_demo_rca_ingress_500_no_longer_matches():
    """The rca_ingress_500 matcher was removed because no distinct canned
    scenario existed for it — the old code silently fell back to the
    lookup_parcels RCA, which would've been misleading at demo time."""
    assert demo.is_demo_query("ingress-service is throwing 500") is None
    assert demo.is_demo_query("Why is ingress 500-ing?") is None


def test_demo_unrecognized_returns_none():
    assert demo.is_demo_query("blah blah random nonsense") is None


def test_demo_unrecognized_falls_back_to_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    async def collect():
        out = []
        async for evt in demo.run_demo("totally unmatched query xyzzy"):
            out.append(evt)
        return out

    events = asyncio.run(collect())
    text = "\n".join(events)
    assert "demo mode is on but this query has no canned scenario" in text
    assert "How do I label a white tape device" in text
    # Hint text now refers to device_event (PG), not lookup_parcels
    assert "device_event" in text


def test_demo_discovery_streams_answer_deltas(tmp_path, monkeypatch):
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    async def collect():
        out = []
        async for evt in demo.run_discovery_demo("discovery_label_white_tape", "How do I label a white tape?"):
            out.append(evt)
        return out

    events = asyncio.run(collect())
    delta_events = [e for e in events if "answer_delta" in e]
    assert len(delta_events) > 5  # streamed in chunks, not one big answer
    citation_events = [e for e in events if "citation_list" in e]
    assert len(citation_events) == 1


def test_demo_discovery_cross_mesh_uses_pg_tool(tmp_path, monkeypatch):
    """cross_customer_mesh scenario must reference trk_postgres_query, not trk_mssql_query."""
    scenario = demo._DISCOVERY_CROSS_CUSTOMER_MESH
    assert "trk_postgres_query" in scenario["answer"]
    assert "trk_mssql_query" not in scenario["answer"]
    assert "trk.configuration" in scenario["answer"] or "application_lookup" in scenario["answer"]


def test_demo_discovery_lime_uses_pg_tables(tmp_path, monkeypatch):
    """lime_selection scenario must reference PG tables (device_event / raw_device_event_info)."""
    scenario = demo._DISCOVERY_LIME_SELECTION
    assert "raw_device_event_info" in scenario["answer"] or "device_event" in scenario["answer"]
    assert "lookup_parcels" not in scenario["answer"]
    assert "proximity_db" not in scenario["answer"]


def test_demo_rca_events_not_in_lookup_uses_pg(tmp_path, monkeypatch):
    """RCA scenario must be fully PG — no MSSQL-only table names as primary references."""
    scenario = demo._RCA_EVENTS_NOT_IN_LOOKUP
    rca_text = scenario["final_rca"]
    assert "trk.device_event" in rca_text
    assert "REUSE_OR_EXPIRED" in rca_text
    assert "location-preprocessor" in rca_text
    # No MSSQL legacy table names as primary references (parenthetical mapping notes are OK)
    assert "tapecfg_db" not in rca_text
    assert "trk.lookup_parcels" not in rca_text   # bare trk.lookup_parcels disallowed
    assert "proximity_db" not in rca_text
    assert "trk_mssql_query" not in rca_text
    # Tool name must be trk_postgres_query
    evidence_tools = [e[0] for e in scenario["evidence"]]
    assert "trk_postgres_query" in evidence_tools
    assert "trk_mssql_query" not in evidence_tools


def test_demo_rca_authz_403_scenario_content():
    """v3 authz scenario must reference PG account table and scope_violation."""
    scenario = demo._RCA_AUTHZ_403_OUT_OF_CHAIN
    rca_text = scenario["final_rca"]
    assert "out_of_chain" in rca_text
    assert "parent_id" in rca_text
    assert "trk.account" in rca_text
    assert "scope_violation" in rca_text
    # Tool name must be trk_postgres_query
    evidence_tools = [e[0] for e in scenario["evidence"]]
    assert "trk_postgres_query" in evidence_tools
    assert "trk_mssql_query" not in evidence_tools


def test_demo_rca_writes_real_scratch_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    async def collect():
        out = []
        async for evt in demo.run_rca_demo(
            "rca_events_not_in_lookup",
            "Device AABBCCDDEEFF events not in device_event in PPE",
            entities={"device_id": "AABBCCDDEEFF", "env": "ppe"},
        ):
            out.append(evt)
        return out

    events = asyncio.run(collect())

    # find the rca_done event and verify the scratch dir really has files
    done_lines = [e for e in events if "rca_done" in e]
    assert len(done_lines) == 1
    import json, re
    payload = re.search(r"data: ({.*})", done_lines[0]).group(1)
    data = json.loads(payload)
    rca_id = data["rca_id"]

    inv_dir = tmp_path / rca_id
    assert (inv_dir / "meta.json").exists()
    assert (inv_dir / "final-rca.md").exists()
    assert (inv_dir / "analysis" / "service-hops.mmd").exists()
    evidence_files = list((inv_dir / "evidence").glob("*"))
    assert len(evidence_files) >= 3

    final = (inv_dir / "final-rca.md").read_text()
    assert "REUSE_OR_EXPIRED" in final
    assert "device_status" in final
    # Must reference PG table
    assert "trk.device_event" in final


def test_demo_rca_authz_writes_real_scratch_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    async def collect():
        out = []
        async for evt in demo.run_rca_demo(
            "rca_authz_403_out_of_chain",
            "User caruld sees empty device list on /dash/v3/devices in PPE",
            entities={"user": "caruld", "env": "ppe"},
        ):
            out.append(evt)
        return out

    events = asyncio.run(collect())

    done_lines = [e for e in events if "rca_done" in e]
    assert len(done_lines) == 1
    import json, re
    payload = re.search(r"data: ({.*})", done_lines[0]).group(1)
    data = json.loads(payload)
    rca_id = data["rca_id"]

    inv_dir = tmp_path / rca_id
    assert (inv_dir / "meta.json").exists()
    assert (inv_dir / "final-rca.md").exists()
    assert (inv_dir / "analysis" / "service-hops.mmd").exists()
    evidence_files = list((inv_dir / "evidence").glob("*"))
    assert len(evidence_files) >= 3

    final = (inv_dir / "final-rca.md").read_text()
    assert "out_of_chain" in final
    assert "parent_id" in final
