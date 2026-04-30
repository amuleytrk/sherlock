"""Unit tests for the trust-layer claim extractor.

Verification (the Haiku call) is exercised in live tests; here we lock down
the deterministic extraction so changes that lose claim coverage are caught
in unit CI."""
from __future__ import annotations

from apps.api.verify import extract_claims, _band


def test_extract_endpoint_basic():
    md = "Use `GET /devices/v1/configs/get_history` to fetch history."
    claims = extract_claims(md)
    kinds = {c.kind for c in claims}
    assert "endpoint" in kinds
    assert any("/devices/v1/configs/get_history" in c.text for c in claims)


def test_extract_endpoint_multiple():
    md = """
- `GET /devices/v1/events/latest`
- `POST /external/messages` (the milestone API)
- `DELETE /sessions/{id}` (a Sherlock route)
"""
    claims = extract_claims(md)
    paths = [c.text for c in claims if c.kind == "endpoint"]
    assert any("/devices/v1/events/latest" in p for p in paths)
    assert any("/external/messages" in p for p in paths)
    # /sessions/{id} comes through too — that's fine, it's a real route.


def test_extract_sql_table():
    md = "Insert into `trk.lookup_parcels` and read `trk.tapecfg_db.tape_id`."
    claims = extract_claims(md)
    sql = [c.text for c in claims if c.kind == "sql_table"]
    assert "trk.lookup_parcels" in sql
    # Two-segment names are also captured at length-2.
    assert any(s.startswith("trk.tapecfg_db") for s in sql)


def test_extract_feature_flag():
    md = "Toggled by `feature_configuration.cross_customer_mesh_allowed`."
    claims = extract_claims(md)
    flags = [c.text for c in claims if c.kind == "feature_flag"]
    assert "feature_configuration.cross_customer_mesh_allowed" in flags


def test_extract_dedupes():
    md = "`GET /api/x` is the same as GET /api/x referenced twice."
    claims = extract_claims(md)
    endpoints = [c.text for c in claims if c.kind == "endpoint"]
    assert len(endpoints) == 1


def test_extract_no_false_positive_on_prose():
    md = "We use customer_id, authorized_group, and standard fields."
    claims = extract_claims(md)
    # Plain backticked tokens like `customer_id` should NOT be claims —
    # too noisy. Only structured shapes (endpoints, trk.* tables, flags)
    # qualify.
    assert claims == [] or all(c.kind != "endpoint" for c in claims)


def test_band_thresholds():
    assert _band(95) == "green"
    assert _band(80) == "green"
    assert _band(79) == "yellow"
    assert _band(50) == "yellow"
    assert _band(49) == "red"
    assert _band(0) == "red"
