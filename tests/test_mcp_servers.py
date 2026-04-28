"""Import + safety smoke tests for all 6 MCP servers.

Live integration tests live in tests/live/ and require real PPE creds; these
import-only tests verify wiring and read-only enforcement.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


SERVER_MODULES = [
    "mcp_servers.trk_kubectl.server",
    "mcp_servers.trk_mssql.server",
    "mcp_servers.trk_cosmos.server",
    "mcp_servers.trk_redis.server",
    "mcp_servers.trk_datadog.server",
    "mcp_servers.sherlock_rag.server",
]


@pytest.mark.parametrize("modname", SERVER_MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)


def test_mssql_template_catalog_has_required_queries():
    from mcp_servers.trk_mssql.templates import QUERY_TEMPLATES
    required = {
        "device_config", "location_history", "device_events_recent",
        "customer_config", "facility_lookup", "feature_flags",
        "duplicate_check", "raw_events_check", "event_delivery_check",
    }
    assert required.issubset(set(QUERY_TEMPLATES.keys()))


def test_mssql_templates_specify_required_params():
    from mcp_servers.trk_mssql.templates import QUERY_TEMPLATES
    for name, spec in QUERY_TEMPLATES.items():
        assert "params" in spec and isinstance(spec["params"], list)
        assert "sql" in spec and "SELECT" in spec["sql"].upper()
        # No DML keywords leaked into a template
        upper = spec["sql"].upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "DROP ", "TRUNCATE "):
            assert forbidden not in upper, f"template {name} contains forbidden keyword {forbidden}"


def test_redis_key_patterns_complete():
    from mcp_servers.trk_redis.server import KEY_PATTERNS
    required = {"idict", "pids_to_limes", "ble_config", "mesh_dedup", "dwell_timer"}
    assert required.issubset(set(KEY_PATTERNS.keys()))
    # Every pattern uses a read-only op
    for kt, spec in KEY_PATTERNS.items():
        assert spec["op"] in {"GET", "HGETALL", "EXISTS", "ZSCORE"}


@pytest.mark.asyncio
async def test_mssql_unknown_query_type_returns_error():
    from mcp_servers.trk_mssql.server import call_tool
    out = await call_tool("query_template", {"query_type": "definitely_not_a_real_query"})
    assert "unknown query_type" in out[0].text


@pytest.mark.asyncio
async def test_mssql_missing_required_params_returns_error():
    from mcp_servers.trk_mssql.server import call_tool
    out = await call_tool("query_template", {"query_type": "device_config", "params": {}})
    assert "missing required params" in out[0].text


@pytest.mark.asyncio
async def test_cosmos_rejects_non_select_query():
    """Even with creds wired up, INSERT/DELETE/etc must be refused at the boundary."""
    from mcp_servers.trk_cosmos.server import call_tool
    out = await call_tool("query_documents", {
        "container": "consumables",
        "query": "DELETE FROM c WHERE c.id = 'x'",
    })
    assert "only SELECT" in out[0].text or "cosmos not configured" in out[0].text


@pytest.mark.asyncio
async def test_cosmos_rejects_select_with_dml_keyword():
    from mcp_servers.trk_cosmos.server import call_tool
    out = await call_tool("query_documents", {
        "container": "consumables",
        "query": "SELECT c.id FROM c WHERE c.foo = 'INSERT INTO z'",  # crafted to embed forbidden word
    })
    # Either it's blocked because of forbidden keyword, or cosmos isn't configured (env)
    txt = out[0].text
    assert "forbidden keyword" in txt or "cosmos not configured" in txt


@pytest.mark.asyncio
async def test_redis_unknown_key_type_returns_error():
    from mcp_servers.trk_redis.server import call_tool
    out = await call_tool("redis_get", {"key_type": "not_a_real_pattern", "params": {}})
    assert "unknown key_type" in out[0].text or "not configured" in out[0].text


@pytest.mark.asyncio
async def test_redis_missing_param_for_pattern():
    """idict requires tape_id; without it we get a missing-param error."""
    from mcp_servers.trk_redis.server import call_tool
    out = await call_tool("redis_get", {"key_type": "idict", "params": {}})
    txt = out[0].text
    assert "missing param" in txt or "not configured" in txt


def test_redis_client_builds_from_host_triplet(monkeypatch):
    """Host + port + key should produce a Redis client without needing a URL."""
    class _FakeSettings:
        redis_ppe_url = ""
        redis_ppe_host = "ppe-redis.example.com"
        redis_ppe_port = 6380
        redis_ppe_key = "fake-key"
        redis_ppe_tls = True

    monkeypatch.setattr("mcp_servers.trk_redis.server.get_settings", lambda: _FakeSettings())
    from mcp_servers.trk_redis.server import _client
    client = _client()
    # Client constructed without raising = test passes; check the connection
    # parameters rather than connecting.
    pool = client.connection_pool
    assert pool.connection_kwargs["host"] == "ppe-redis.example.com"
    assert pool.connection_kwargs["port"] == 6380
    assert pool.connection_kwargs["password"] == "fake-key"


def test_redis_client_builds_from_url(monkeypatch):
    """REDIS_PPE_URL should still work for users who already have a URL form."""
    class _FakeSettings:
        redis_ppe_url = "rediss://:secret@host.example.com:6380"
        redis_ppe_host = ""
        redis_ppe_port = 6380
        redis_ppe_key = ""
        redis_ppe_tls = True

    monkeypatch.setattr("mcp_servers.trk_redis.server.get_settings", lambda: _FakeSettings())
    from mcp_servers.trk_redis.server import _client
    client = _client()
    assert client is not None


def test_redis_client_raises_with_no_config(monkeypatch):
    class _FakeSettings:
        redis_ppe_url = ""
        redis_ppe_host = ""
        redis_ppe_port = 6380
        redis_ppe_key = ""
        redis_ppe_tls = True

    monkeypatch.setattr("mcp_servers.trk_redis.server.get_settings", lambda: _FakeSettings())
    from mcp_servers.trk_redis.server import _client
    with pytest.raises(RuntimeError, match="Redis not configured"):
        _client()


def test_redis_triplet_takes_precedence_over_url(monkeypatch):
    """If both URL and triplet are set, triplet wins (clearer & avoids URL-encoding)."""
    class _FakeSettings:
        redis_ppe_url = "rediss://:other-key@other-host:9999"
        redis_ppe_host = "preferred-host.example.com"
        redis_ppe_port = 6380
        redis_ppe_key = "preferred-key"
        redis_ppe_tls = True

    monkeypatch.setattr("mcp_servers.trk_redis.server.get_settings", lambda: _FakeSettings())
    from mcp_servers.trk_redis.server import _client
    client = _client()
    pool = client.connection_pool
    assert pool.connection_kwargs["host"] == "preferred-host.example.com"
    assert pool.connection_kwargs["password"] == "preferred-key"
