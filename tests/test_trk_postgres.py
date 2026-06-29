"""Offline tests for the trk_postgres MCP server package.

All tests in this module run without any database or network access.
The live smoke test at the bottom is skipped unless RUN_LIVE=1 is set.
"""
from __future__ import annotations

import os
import pytest

from mcp_servers.trk_postgres.uuids import account_uuid, application_uuid
from mcp_servers.trk_postgres.templates import CATALOG


# ---------------------------------------------------------------------------
# UUID helpers
# ---------------------------------------------------------------------------

class TestAccountUuid:
    def test_known_value(self):
        """Confirmed against live trk.account.id rows in PPE (ground truth doc)."""
        assert account_uuid("123456", "abcd") == "359c58d5-d52f-211d-cc28-384a0c96bef7"

    def test_format_36_chars(self):
        result = account_uuid("123456", "abcd")
        assert len(result) == 36

    def test_format_parts(self):
        result = account_uuid("123456", "abcd")
        parts = result.split("-")
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]

    def test_uses_hash_separator(self):
        """Formula uses '#' between customer_id and authorized_group."""
        import hashlib
        raw = hashlib.md5("123456#abcd".encode()).hexdigest()
        h = raw
        expected = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        assert account_uuid("123456", "abcd") == expected

    def test_different_inputs_give_different_uuids(self):
        a = account_uuid("111", "aaa")
        b = account_uuid("222", "bbb")
        assert a != b

    def test_deterministic(self):
        assert account_uuid("foo", "bar") == account_uuid("foo", "bar")


class TestApplicationUuid:
    def test_format_36_chars(self):
        result = application_uuid("123456", "abcd", "my-app")
        assert len(result) == 36

    def test_format_parts(self):
        result = application_uuid("123456", "abcd", "my-app")
        parts = result.split("-")
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]

    def test_uses_hash_separator(self):
        """Formula uses '#' between all three components."""
        import hashlib
        raw = hashlib.md5("123456#abcd#my-app".encode()).hexdigest()
        h = raw
        expected = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        assert application_uuid("123456", "abcd", "my-app") == expected

    def test_differs_from_account_uuid(self):
        """application_uuid has an extra component so must differ from account_uuid."""
        assert application_uuid("123456", "abcd", "x") != account_uuid("123456", "abcd")

    def test_deterministic(self):
        assert (
            application_uuid("foo", "bar", "baz")
            == application_uuid("foo", "bar", "baz")
        )


# ---------------------------------------------------------------------------
# CATALOG structure
# ---------------------------------------------------------------------------

class TestCatalogStructure:
    def test_has_exactly_12_templates(self):
        assert len(CATALOG) == 12

    def test_expected_keys(self):
        expected = {
            "device_config",
            "location_history",
            "device_events_recent",
            "raw_events_check",
            "customer_config",
            "feature_flags",
            "facility_lookup",
            "duplicate_check",
            "device_health",
            "event_delivery_check",
            "account_lookup",
            "application_lookup",
        }
        assert set(CATALOG.keys()) == expected

    def test_all_templates_have_required_fields(self):
        for name, tmpl in CATALOG.items():
            assert "description" in tmpl, f"{name} missing description"
            assert "required" in tmpl, f"{name} missing required"
            assert "optional" in tmpl, f"{name} missing optional"
            assert "sql" in tmpl, f"{name} missing sql"
            assert isinstance(tmpl["required"], list), f"{name}.required not a list"
            assert isinstance(tmpl["optional"], list), f"{name}.optional not a list"


# ---------------------------------------------------------------------------
# SQL correctness checks
# ---------------------------------------------------------------------------

class TestSqlCorrectness:
    def test_all_templates_have_limit(self):
        for name, tmpl in CATALOG.items():
            assert "LIMIT" in tmpl["sql"].upper(), f"{name} missing LIMIT"

    def test_no_vendor_config(self):
        for name, tmpl in CATALOG.items():
            assert "vendor_config" not in tmpl["sql"].lower(), (
                f"{name} references vendor_config (not in trk schema)"
            )

    def test_no_mssql_select_top(self):
        for name, tmpl in CATALOG.items():
            assert "SELECT TOP" not in tmpl["sql"].upper(), (
                f"{name} uses MSSQL SELECT TOP syntax"
            )

    def test_no_mssql_json_value(self):
        for name, tmpl in CATALOG.items():
            assert "JSON_VALUE" not in tmpl["sql"].upper(), (
                f"{name} uses MSSQL JSON_VALUE syntax"
            )

    def test_no_mssql_square_brackets(self):
        for name, tmpl in CATALOG.items():
            assert "[" not in tmpl["sql"], (
                f"{name} uses MSSQL square-bracket identifiers"
            )

    def test_no_forbidden_tables(self):
        for name, tmpl in CATALOG.items():
            sql = tmpl["sql"].lower()
            assert "lookup_parcels" not in sql, f"{name} references lookup_parcels"
            assert "tapecfg_db" not in sql, f"{name} references tapecfg_db"
            assert "proximity_db" not in sql, f"{name} references proximity_db"

    def test_uses_named_params_style(self):
        """All parameter placeholders must use %(name)s psycopg named style."""
        import re
        positional_pattern = re.compile(r"(?<!\w)%s(?!\w)")
        for name, tmpl in CATALOG.items():
            match = positional_pattern.search(tmpl["sql"])
            assert match is None, (
                f"{name} uses positional %s style — must use %(name)s"
            )

    def test_schema_qualified_tables(self):
        """All table references must be schema-qualified with trk."""
        # Core tables that must appear schema-qualified when referenced
        tables_used = {
            "device_config": ["trk.device", "trk.account", "trk.application", "trk.configuration"],
            "location_history": ["trk.device_event"],
            "device_events_recent": ["trk.raw_device_event", "trk.raw_device_event_info"],
            "raw_events_check": ["trk.raw_device_event"],
            "customer_config": ["trk.account", "trk.application"],
            "feature_flags": ["trk.configuration", "trk.application", "trk.account"],
            "facility_lookup": ["trk.facility"],
            "duplicate_check": ["trk.device", "trk.account"],
            "device_health": ["trk.device_health_history"],
            "event_delivery_check": ["trk.device_event"],
            "account_lookup": ["trk.account"],
            "application_lookup": ["trk.account", "trk.application"],
        }
        for name, tables in tables_used.items():
            sql = CATALOG[name]["sql"]
            for table in tables:
                assert table in sql, f"{name} missing schema-qualified {table}"

    def test_device_event_templates_have_year_param(self):
        """Templates querying device_event must expose a 'year' param."""
        device_event_templates = [
            "device_events_recent",
            "raw_events_check",
            "device_health",
            "event_delivery_check",
        ]
        for name in device_event_templates:
            tmpl = CATALOG[name]
            all_params = tmpl.get("required", []) + tmpl.get("optional", [])
            assert "year" in all_params, (
                f"{name} missing year param (needed for device_event partition pruning)"
            )

    def test_raw_device_event_info_joins_on_partition_and_id(self):
        """raw_device_event_info must join on both (partition, id) — not just id."""
        sql = CATALOG["device_events_recent"]["sql"]
        # Both columns must appear in a join context
        assert "r.partition = i.partition" in sql
        assert "r.id = i.id" in sql

    def test_location_history_has_year_param(self):
        tmpl = CATALOG["location_history"]
        all_params = tmpl.get("required", []) + tmpl.get("optional", [])
        assert "year" in all_params, "location_history missing year param"


# ---------------------------------------------------------------------------
# Read-only enforcement
# ---------------------------------------------------------------------------

class TestReadOnlyEnforcement:
    def test_connect_kwargs_includes_read_only_guc(self):
        from mcp_servers.trk_postgres.server import build_connect_kwargs
        from apps.api.env_context import EnvCreds

        fake_cfg = EnvCreds(
            env="test",
            pg_host="fake-host",
            pg_port=5432,
            pg_database="fake-db",
            pg_user="fake-user",
            pg_password="fake-pass",
            pg_sslmode="require",
            pg_search_path="trk",
        )
        kwargs = build_connect_kwargs(fake_cfg)
        assert "default_transaction_read_only=on" in kwargs.get("options", ""), (
            "build_connect_kwargs must set default_transaction_read_only=on in options"
        )

    def test_connect_kwargs_includes_search_path(self):
        from mcp_servers.trk_postgres.server import build_connect_kwargs
        from apps.api.env_context import EnvCreds

        fake_cfg = EnvCreds(
            env="test",
            pg_host="fake-host",
            pg_port=5432,
            pg_database="fake-db",
            pg_user="fake-user",
            pg_password="fake-pass",
            pg_sslmode="require",
            pg_search_path="trk",
        )
        kwargs = build_connect_kwargs(fake_cfg)
        assert "search_path=trk" in kwargs.get("options", ""), (
            "build_connect_kwargs must set search_path in options"
        )

    def test_connect_kwargs_uses_cfg_sslmode(self):
        from mcp_servers.trk_postgres.server import build_connect_kwargs
        from apps.api.env_context import EnvCreds

        fake_cfg = EnvCreds(
            env="test",
            pg_host="fake-host",
            pg_port=5432,
            pg_database="fake-db",
            pg_user="fake-user",
            pg_password="fake-pass",
            pg_sslmode="require",
            pg_search_path="trk",
        )
        kwargs = build_connect_kwargs(fake_cfg)
        assert kwargs["sslmode"] == "require"


# ---------------------------------------------------------------------------
# Live smoke test (skipped unless RUN_LIVE=1)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE"),
    reason="requires live PG PPE DB (set RUN_LIVE=1 to enable)",
)
def test_device_config_live():
    """Smoke test: run device_config against PPE for a known device_id."""
    import json
    from mcp_servers.trk_postgres.server import _connect
    from mcp_servers.trk_postgres.templates import CATALOG

    device_id = os.environ.get("TEST_DEVICE_ID", "")
    if not device_id:
        pytest.skip("Set TEST_DEVICE_ID to a known PPE device_id to run this test")

    spec = CATALOG["device_config"]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(spec["sql"], {"device_id": device_id})
            rows = cur.fetchall()

    assert len(rows) >= 1, f"No device_config rows for device_id={device_id!r}"
    row = rows[0]
    assert row["device_id"] == device_id
    print(json.dumps(rows, default=str, indent=2))
