"""Tests for the investigation scratch-dir manager."""
from __future__ import annotations

import json
from pathlib import Path

from apps.api.agents.scratch import Investigation


def test_create_investigation_creates_dirs(tmp_path):
    inv = Investigation.create(
        root=tmp_path,
        rca_id="rca_001",
        user_query="device AABB events not in lookup_parcels",
        entities={"tape_id": "AABBCCDDEEFF", "env": "ppe"},
    )
    assert (tmp_path / "rca_001").exists()
    assert (tmp_path / "rca_001" / "evidence").exists()
    assert (tmp_path / "rca_001" / "analysis").exists()
    meta = json.loads((tmp_path / "rca_001" / "meta.json").read_text())
    assert meta["rca_id"] == "rca_001"
    assert meta["entities"]["tape_id"] == "AABBCCDDEEFF"


def test_write_evidence_uses_sequential_numbering(tmp_path):
    inv = Investigation.create(root=tmp_path, rca_id="r2", user_query="q", entities={})
    p1 = inv.write_evidence("mssql-device-config", "json", '{"ok": 1}')
    p2 = inv.write_evidence("kubectl-ingress-logs", "txt", "log line\n")
    assert p1.name.startswith("001-")
    assert p2.name.startswith("002-")
    assert p1.read_text() == '{"ok": 1}'


def test_write_final_rca(tmp_path):
    inv = Investigation.create(root=tmp_path, rca_id="r3", user_query="q", entities={})
    inv.write_final_rca("# Root cause\n…")
    final = (tmp_path / "r3" / "final-rca.md").read_text()
    assert final.startswith("# Root cause")


def test_list_evidence(tmp_path):
    inv = Investigation.create(root=tmp_path, rca_id="r4", user_query="q", entities={})
    inv.write_evidence("a", "json", "{}")
    inv.write_evidence("b", "json", "{}")
    files = inv.list_evidence()
    assert len(files) == 2
    assert files[0].name.startswith("001-")


def test_load_existing_investigation(tmp_path):
    Investigation.create(root=tmp_path, rca_id="r5", user_query="q", entities={"x": 1})
    again = Investigation.load(root=tmp_path, rca_id="r5")
    assert again.read_meta()["entities"] == {"x": 1}


def test_write_analysis_handles_bytes_and_text(tmp_path):
    inv = Investigation.create(root=tmp_path, rca_id="r6", user_query="q", entities={})
    inv.write_analysis("notes.md", "# notes\nfoo")
    inv.write_analysis("chart.png", b"\x89PNG\r\n\x1a\n...")
    assert (tmp_path / "r6" / "analysis" / "notes.md").read_text().startswith("# notes")
    assert (tmp_path / "r6" / "analysis" / "chart.png").read_bytes().startswith(b"\x89PNG")
