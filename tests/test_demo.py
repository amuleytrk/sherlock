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


def test_demo_rca_events_not_in_lookup_matches():
    assert demo.is_demo_query("Device AABB events not in lookup_parcels in PPE") == "rca_events_not_in_lookup"
    assert demo.is_demo_query("not appearing in lookup_parcels") == "rca_events_not_in_lookup"


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


def test_demo_rca_writes_real_scratch_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path))
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    async def collect():
        out = []
        async for evt in demo.run_rca_demo(
            "rca_events_not_in_lookup",
            "Device AABBCCDDEEFF events not in lookup_parcels in PPE",
            entities={"tape_id": "AABBCCDDEEFF", "env": "ppe"},
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
