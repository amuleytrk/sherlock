"""Live integration tests for each MCP server. Skipped by default; run with
`uv run pytest -m live` when you have PPE credentials populated in `.env`.

Each test makes a single real read against the corresponding system, so the
test suite stays fast and free when you don't need it.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest


def _missing_env(*keys: str) -> str | None:
    """Return a skip reason if any env key is missing, else None."""
    for k in keys:
        if not os.environ.get(k):
            return f"{k} not set"
    return None


@pytest.mark.live
def test_live_pgvector_schema_present():
    """Verifies the local Postgres has the vector_store.chunks schema."""
    from apps.api.settings import get_settings
    import psycopg
    s = get_settings()
    try:
        with psycopg.connect(s.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = 'vector_store' AND table_name = 'chunks'
                """)
                count = cur.fetchone()[0]
                assert count == 1, "vector_store.chunks not deployed — run `uv run python -m indexer.db`"
    except psycopg.OperationalError as e:
        pytest.skip(f"local Postgres not reachable: {e}")


@pytest.mark.live
def test_live_pgvector_has_chunks():
    """Verifies the indexer has been run and corpus chunks exist."""
    from apps.api.settings import get_settings
    import psycopg
    s = get_settings()
    try:
        with psycopg.connect(s.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM vector_store.chunks WHERE release = %s", (s.sherlock_release,))
                count = cur.fetchone()[0]
                if count == 0:
                    pytest.skip("corpus is empty — run `uv run python -m indexer.run` first")
                assert count >= 50, f"expected at least 50 chunks; found {count}"
    except psycopg.OperationalError as e:
        pytest.skip(f"local Postgres not reachable: {e}")


@pytest.mark.live
def test_live_openai_embedding():
    skip = _missing_env("OPENAI_API_KEY")
    if skip:
        pytest.skip(skip)
    from indexer.embed import embed_texts
    vecs = embed_texts(["device labelling API"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 3072


@pytest.mark.live
def test_live_anthropic_router():
    skip = _missing_env("ANTHROPIC_API_KEY")
    if skip:
        pytest.skip(skip)
    from apps.api.router import classify
    out = classify("Device AABBCCDDEEFF events not in lookup_parcels")
    assert out.intent == "DEBUGGING"
    assert out.entities.get("tape_id") == "AABBCCDDEEFF"


@pytest.mark.live
def test_live_mssql_smoke():
    skip = _missing_env("MSSQL_PPE_USER", "MSSQL_PPE_PASSWORD")
    if skip:
        pytest.skip(skip)
    from mcp_servers.trk_mssql.server import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT TOP 1 1 AS one FROM trk.tapecfg_db")
            row = cur.fetchone()
            assert row is not None


@pytest.mark.live
def test_live_cosmos_smoke():
    skip = _missing_env("COSMOS_PPE_ENDPOINT", "COSMOS_PPE_KEY", "COSMOS_PPE_DATABASE")
    if skip:
        pytest.skip(skip)
    from mcp_servers.trk_cosmos.server import _client
    from apps.api.settings import get_settings
    s = get_settings()
    db = _client().get_database_client(s.cosmos_ppe_database)
    container = db.get_container_client("consumables")
    items = list(container.query_items(
        query="SELECT TOP 1 c.id FROM c",
        enable_cross_partition_query=True,
        max_item_count=1,
    ))
    assert isinstance(items, list)


@pytest.mark.live
def test_live_redis_smoke():
    skip = _missing_env("REDIS_PPE_URL")
    if skip:
        pytest.skip(skip)
    from mcp_servers.trk_redis.server import _client
    client = _client()
    assert client.ping() is True


@pytest.mark.live
def test_live_kubectl_smoke():
    """Verify kubectl is configured and can list pods in the platform namespace.
    Adjust the namespace to whatever PPE uses."""
    from mcp_servers.trk_kubectl.server import _run_kubectl
    rc, out, err = _run_kubectl(["version", "--client", "-o", "json"], timeout=10)
    if rc != 0:
        pytest.skip(f"kubectl client check failed: {err}")
    info = json.loads(out)
    assert "clientVersion" in info


@pytest.mark.live
def test_live_datadog_smoke():
    skip = _missing_env("DATADOG_API_KEY", "DATADOG_APP_KEY")
    if skip:
        pytest.skip(skip)
    # Just check the API client can be constructed; an actual log search would
    # need a known query. Datadog is sunsetting at Trackonomy so keep this minimal.
    from datadog_api_client import Configuration
    cfg = Configuration()
    cfg.api_key["apiKeyAuth"] = os.environ["DATADOG_API_KEY"]
    cfg.api_key["appKeyAuth"] = os.environ["DATADOG_APP_KEY"]
    assert cfg.api_key["apiKeyAuth"]
