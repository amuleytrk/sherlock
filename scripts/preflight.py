"""Pre-flight checks for Sherlock.

Verifies every external dependency works BEFORE you run the indexer or burn
any meaningful API credit. Each check is bounded:

- OpenAI: 1 tiny embedding call (~ $0.0001)
- Anthropic: 1 tiny Haiku call (~ $0.0005)
- MSSQL / Cosmos / Redis: a single read; no writes ever
- kubectl: list ppe namespace pods (read-only)
- Datadog: skipped if keys absent (this is OK — Sherlock works without it)

Usage:
    uv run python -m scripts.preflight
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Callable


# ANSI color helpers (stdout only)
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str:   return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def _gray(s: str) -> str:  return f"\033[90m{s}\033[0m"


def _check(name: str, cost_hint: str, fn: Callable[[], str]) -> tuple[str, str | None, float]:
    """Run a check; return (status, detail, duration_ms)."""
    t0 = time.monotonic()
    try:
        detail = fn()
        return ("ok", detail, (time.monotonic() - t0) * 1000)
    except _Skip as e:
        return ("skip", str(e), (time.monotonic() - t0) * 1000)
    except Exception as e:
        return ("fail", f"{type(e).__name__}: {e}", (time.monotonic() - t0) * 1000)


class _Skip(Exception):
    pass


# ---- individual checks ----


def check_pgvector() -> str:
    import psycopg
    from apps.api.settings import get_settings
    s = get_settings()
    with psycopg.connect(s.database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("pgvector extension not installed; run `uv run python -m indexer.db`")
            cur.execute("SELECT count(*) FROM vector_store.chunks")
            chunk_count = cur.fetchone()[0]
    return f"pgvector v{row[0]} · {chunk_count} chunks indexed"


def check_openai() -> str:
    from openai import OpenAI
    from apps.api.settings import get_settings
    s = get_settings()
    if not s.openai_api_key:
        raise _Skip("OPENAI_API_KEY not set")
    client = OpenAI(api_key=s.openai_api_key, timeout=10.0)
    r = client.embeddings.create(
        model="text-embedding-3-large", input=["preflight"], dimensions=3072,
    )
    return f"text-embedding-3-large OK · dim={len(r.data[0].embedding)} · usage tokens={r.usage.total_tokens}"


def check_anthropic_haiku() -> str:
    from anthropic import Anthropic
    from apps.api.settings import get_settings
    s = get_settings()
    if not s.anthropic_api_key:
        raise _Skip("ANTHROPIC_API_KEY not set")
    client = Anthropic(api_key=s.anthropic_api_key, timeout=10.0)
    r = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with exactly the word OK"}],
    )
    text = "".join(getattr(b, "text", "") for b in r.content)
    return f"haiku-4-5 OK · response={text!r} · in={r.usage.input_tokens} out={r.usage.output_tokens}"


def check_mssql() -> str:
    from mcp_servers.trk_mssql.server import _connect
    from apps.api.settings import get_settings
    s = get_settings()
    if not (s.mssql_ppe_user and s.mssql_ppe_password):
        raise _Skip("MSSQL credentials not set")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT TOP 1 1 AS ok FROM trk.tapecfg_db")
            row = cur.fetchone()
    return f"connected to {s.mssql_ppe_database} · trk.tapecfg_db readable" if row else "connected but no rows"


def check_cosmos() -> str:
    from mcp_servers.trk_cosmos.server import _client
    from apps.api.settings import get_settings
    s = get_settings()
    if not (s.cosmos_ppe_endpoint and s.cosmos_ppe_key and s.cosmos_ppe_database):
        raise _Skip("Cosmos credentials not set")
    db = _client().get_database_client(s.cosmos_ppe_database)
    container = db.get_container_client("consumables")
    items = list(container.query_items(
        query="SELECT TOP 1 c.id FROM c",
        enable_cross_partition_query=True,
        max_item_count=1,
    ))
    return f"connected to {s.cosmos_ppe_database} · consumables container readable · sample id={items[0]['id'] if items else '(empty)'}"


def check_redis() -> str:
    from mcp_servers.trk_redis.server import _client
    from apps.api.settings import get_settings
    s = get_settings()
    if not ((s.redis_ppe_host and s.redis_ppe_key) or s.redis_ppe_url):
        raise _Skip("Redis credentials not set")
    client = _client()
    pong = client.ping()
    return f"connected ({s.redis_ppe_host or 'via URL'}) · ping={pong}"


def check_kubectl() -> str:
    from mcp_servers.trk_kubectl.server import _run_kubectl
    from apps.api.settings import get_settings
    s = get_settings()
    if not s.kubeconfig and not os.environ.get("KUBECONFIG"):
        raise _Skip("KUBECONFIG not set; default ~/.kube/config will be used by Sherlock")
    rc, out, err = _run_kubectl(["get", "namespaces", "-o", "name"], timeout=15)
    if rc != 0:
        raise RuntimeError(f"kubectl failed: {err.strip()}")
    namespaces = [n.split("/", 1)[-1] for n in out.splitlines()]
    has_ppe = "ppe" in namespaces
    return f"kubectl OK · {len(namespaces)} namespaces visible · ppe namespace {'present ✓' if has_ppe else 'NOT FOUND ✗'}"


def check_datadog() -> str:
    from apps.api.settings import get_settings
    s = get_settings()
    if not (s.datadog_api_key and s.datadog_app_key):
        raise _Skip("Datadog keys not set (Sherlock works fine without it — kubectl is the primary log source)")
    # Don't burn a real query — just verify the SDK can be configured.
    from datadog_api_client import Configuration
    cfg = Configuration()
    cfg.api_key["apiKeyAuth"] = s.datadog_api_key
    cfg.api_key["appKeyAuth"] = s.datadog_app_key
    return f"keys present · site={s.datadog_site}"


# ---- runner ----


CHECKS: list[tuple[str, str, Callable[[], str]]] = [
    ("Postgres + pgvector",        "$0",       check_pgvector),
    ("OpenAI embeddings",          "~$0.0001", check_openai),
    ("Anthropic Haiku 4.5",        "~$0.0005", check_anthropic_haiku),
    ("MSSQL PPE (dbtrkmtppe2)",    "$0",       check_mssql),
    ("Cosmos PPE",                 "$0",       check_cosmos),
    ("Redis PPE",                  "$0",       check_redis),
    ("kubectl PPE cluster",        "$0",       check_kubectl),
    ("Datadog (optional)",         "$0",       check_datadog),
]


def main():
    print(_gray("Sherlock preflight — validates every external dependency."))
    print(_gray("Total estimated API spend for this run: < $0.001"))
    print()

    results = []
    for name, cost_hint, fn in CHECKS:
        print(f"  {name:<32} ", end="", flush=True)
        status, detail, duration = _check(name, cost_hint, fn)
        if status == "ok":
            badge = _green("OK  ")
        elif status == "skip":
            badge = _yellow("SKIP")
        else:
            badge = _red("FAIL")
        print(f"{badge} {duration:>6.0f}ms  {_gray(cost_hint):<10}  {detail}")
        results.append((name, status))

    print()
    failures = [n for n, s in results if s == "fail"]
    if failures:
        print(_red(f"✗ {len(failures)} failure(s): {', '.join(failures)}"))
        print(_red("Fix these before running the indexer."))
        sys.exit(1)

    skips = [n for n, s in results if s == "skip"]
    if skips:
        print(_yellow(f"⚠ {len(skips)} skipped (likely missing creds): {', '.join(skips)}"))
        print(_yellow("These will gracefully degrade in Sherlock — the agent just won't use them."))

    print(_green(f"✓ all configured checks passed"))


if __name__ == "__main__":
    main()
