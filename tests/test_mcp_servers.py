"""Import + safety smoke tests for MCP servers.

Live integration tests live in tests/live/ and require real PPE creds; these
import-only tests verify wiring and read-only enforcement.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


SERVER_MODULES = [
    "mcp_servers.trk_kubectl.server",
    "mcp_servers.trk_postgres.server",
    "mcp_servers.trk_cosmos.server",
    "mcp_servers.trk_redis.server",
    "mcp_servers.trk_datadog.server",
    "mcp_servers.sherlock_rag.server",
]


@pytest.mark.parametrize("modname", SERVER_MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)


def test_pg_template_catalog_has_required_queries():
    from mcp_servers.trk_postgres.templates import CATALOG
    required = {
        "device_config", "location_history", "device_events_recent",
        "customer_config", "facility_lookup", "feature_flags",
        "duplicate_check", "raw_events_check", "event_delivery_check",
        "device_health", "account_lookup", "application_lookup",
    }
    assert required.issubset(set(CATALOG.keys()))


def test_pg_templates_specify_required_params():
    from mcp_servers.trk_postgres.templates import CATALOG
    for name, spec in CATALOG.items():
        assert "required" in spec and isinstance(spec["required"], list)
        assert "optional" in spec and isinstance(spec["optional"], list)
        assert "sql" in spec and "SELECT" in spec["sql"].upper()
        # No DML keywords leaked into a template
        upper = spec["sql"].upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "DROP ", "TRUNCATE "):
            assert forbidden not in upper, f"template {name} contains forbidden keyword {forbidden}"


def test_redis_key_patterns_complete():
    from mcp_servers.trk_redis.server import KEY_PATTERNS
    required = {"idict", "pids_to_limes", "ble_config", "mesh_dedup", "dwell_timer", "offline_heartbeat"}
    assert required.issubset(set(KEY_PATTERNS.keys()))
    # Every pattern uses a read-only op
    for kt, spec in KEY_PATTERNS.items():
        assert spec["op"] in {"GET", "HGETALL", "EXISTS", "ZSCORE"}


def test_redis_key_pattern_correctness():
    """Verify the live-verified key patterns are exact — bugs here cause silent miss on real keys."""
    from mcp_servers.trk_redis.server import KEY_PATTERNS
    # mesh_dedup must use the release_2.1 prefix (not the legacy 'meshDeduping')
    assert KEY_PATTERNS["mesh_dedup"]["pattern"].startswith("mobileGatewayDeduping:")
    # dwell_timer key must NOT embed zone_id — zone_id is the ZSCORE member argument
    assert "zone_id" not in KEY_PATTERNS["dwell_timer"]["pattern"]
    assert "zone_id" not in KEY_PATTERNS["dwell_timer"]["params"]
    # idict uses device_id (not tape_id)
    assert "device_id" in KEY_PATTERNS["idict"]["params"]
    assert "tape_id" not in KEY_PATTERNS["idict"]["params"]
    # offline_heartbeat confirmed live in PPE
    assert KEY_PATTERNS["offline_heartbeat"]["pattern"] == "OFFLINEHEARTBEAT:{device_id}"


def test_cosmos_containers_complete():
    """Verify the RCA-relevant container set matches live ground truth."""
    from mcp_servers.trk_cosmos.server import _CONTAINERS, _QUERY_ONLY_CONTAINERS
    required = {"consumables", "infrastructure", "health-history", "deviations",
                "devices", "booking", "organizations", "inventory"}
    assert required == set(_CONTAINERS), f"container mismatch: {set(_CONTAINERS) ^ required}"
    # 'health' 404s on live PPE — must not be present
    assert "health" not in _CONTAINERS
    # query-only containers must be in the allowed set
    assert _QUERY_ONLY_CONTAINERS.issubset(set(_CONTAINERS))


@pytest.mark.asyncio
async def test_pg_unknown_query_type_returns_error():
    from mcp_servers.trk_postgres.server import call_tool
    out = await call_tool("query_template", {"query_type": "definitely_not_a_real_query"})
    assert "unknown query_type" in out[0].text


@pytest.mark.asyncio
async def test_pg_missing_required_params_returns_error():
    from mcp_servers.trk_postgres.server import call_tool
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
    """idict requires device_id; without it we get a missing-param error."""
    from mcp_servers.trk_redis.server import call_tool
    out = await call_tool("redis_get", {"key_type": "idict", "params": {}})
    txt = out[0].text
    assert "missing param" in txt or "not configured" in txt


def test_redis_client_builds_from_host_triplet():
    """Host + port + key should produce a Redis client without needing a URL."""
    from apps.api.env_context import EnvCreds
    from mcp_servers.trk_redis.server import _client
    cfg = EnvCreds(
        env="ppe", redis_url="",
        redis_host="ppe-redis.example.com", redis_port=6380,
        redis_key="fake-key", redis_tls=True,
    )
    client = _client(cfg)
    pool = client.connection_pool
    assert pool.connection_kwargs["host"] == "ppe-redis.example.com"
    assert pool.connection_kwargs["port"] == 6380
    assert pool.connection_kwargs["password"] == "fake-key"


def test_redis_client_builds_from_url():
    """URL form should still work for users who already have a connection string."""
    from apps.api.env_context import EnvCreds
    from mcp_servers.trk_redis.server import _client
    cfg = EnvCreds(
        env="ppe", redis_url="rediss://:secret@host.example.com:6380",
        redis_host="", redis_port=6380, redis_key="", redis_tls=True,
    )
    client = _client(cfg)
    assert client is not None


def test_redis_client_raises_with_no_config():
    from apps.api.env_context import EnvCreds
    from mcp_servers.trk_redis.server import _client
    cfg = EnvCreds(env="stage", redis_url="", redis_host="", redis_port=6380, redis_key="")
    with pytest.raises(RuntimeError, match="Redis not configured"):
        _client(cfg)


def test_redis_triplet_takes_precedence_over_url():
    """If both URL and triplet are set, triplet wins (clearer & avoids URL-encoding)."""
    from apps.api.env_context import EnvCreds
    from mcp_servers.trk_redis.server import _client
    cfg = EnvCreds(
        env="ppe",
        redis_url="rediss://:other-key@other-host:9999",
        redis_host="preferred-host.example.com", redis_port=6380,
        redis_key="preferred-key", redis_tls=True,
    )
    client = _client(cfg)
    pool = client.connection_pool
    assert pool.connection_kwargs["host"] == "preferred-host.example.com"
    assert pool.connection_kwargs["password"] == "preferred-key"
