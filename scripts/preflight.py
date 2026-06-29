"""Pre-flight checks for Sherlock.

Verifies every external dependency works BEFORE you run the indexer or burn
any meaningful API credit. Each check is bounded:

- OpenAI: 1 tiny embedding call (~ $0.0001)
- Anthropic: 1 tiny Haiku call (~ $0.0005)
- Per-env (PostgreSQL / Cosmos / Redis / kubectl): a single read; no writes ever
- Datadog: skipped if keys absent (this is OK — Sherlock works without it)

Multi-env: per-env tool checks run once for each env in `SHERLOCK_ENVS`. So if
`SHERLOCK_ENVS=stage,ppe` you'll see two PostgreSQL checks, two kubectl checks, etc.

Usage:
    uv run python -m scripts.preflight
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Callable

from apps.api.env_context import EnvCreds


# ANSI color helpers (stdout only)
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str:   return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def _gray(s: str) -> str:  return f"\033[90m{s}\033[0m"


def _check(fn: Callable[[], str]) -> tuple[str, str | None, float]:
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


# ---- env-agnostic checks ----


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


def check_datadog() -> str:
    from apps.api.settings import get_settings
    s = get_settings()
    if not (s.datadog_api_key and s.datadog_app_key):
        raise _Skip("Datadog keys not set (Sherlock works fine without it — kubectl is the primary log source)")
    from datadog_api_client import Configuration
    cfg = Configuration()
    cfg.api_key["apiKeyAuth"] = s.datadog_api_key
    cfg.api_key["appKeyAuth"] = s.datadog_app_key
    return f"keys present · site={s.datadog_site}"


# ---- per-env checks (closure over the active env's EnvCreds) ----


def _mk_postgres(cfg: EnvCreds):
    def fn() -> str:
        if not (cfg.pg_host and cfg.pg_user and cfg.pg_password):
            raise _Skip(f"PG_{cfg.env.upper()}_* not set")
        import psycopg
        from mcp_servers.trk_postgres.server import build_connect_kwargs
        with psycopg.connect(**build_connect_kwargs(cfg)) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT device_id FROM trk.device LIMIT 1")
                row = cur.fetchone()
        return f"connected to {cfg.pg_database} (schema trk, read-only) · trk.device readable"
    return fn


def _mk_cosmos(cfg: EnvCreds):
    def fn() -> str:
        if not (cfg.cosmos_endpoint and cfg.cosmos_key and cfg.cosmos_database):
            raise _Skip(f"COSMOS_{cfg.env.upper()}_* not set")
        from azure.cosmos import CosmosClient
        db = CosmosClient(cfg.cosmos_endpoint, credential=cfg.cosmos_key).get_database_client(cfg.cosmos_database)
        container = db.get_container_client("consumables")
        items = list(container.query_items(
            query="SELECT TOP 1 c.id FROM c",
            enable_cross_partition_query=True, max_item_count=1,
        ))
        return f"connected to {cfg.cosmos_database} · consumables container readable · sample id={items[0]['id'] if items else '(empty)'}"
    return fn


def _mk_redis(cfg: EnvCreds):
    def fn() -> str:
        if not ((cfg.redis_host and cfg.redis_key) or cfg.redis_url):
            raise _Skip(f"REDIS_{cfg.env.upper()}_* not set")
        from mcp_servers.trk_redis.server import _client
        pong = _client(cfg).ping()
        return f"connected ({cfg.redis_host or 'via URL'}) · ping={pong}"
    return fn


def _mk_kubectl(cfg: EnvCreds):
    def fn() -> str:
        if not cfg.kubeconfig:
            raise _Skip(f"KUBECONFIG_{cfg.env.upper()} not set")
        if not os.path.isfile(cfg.kubeconfig):
            raise RuntimeError(f"kubeconfig file missing: {cfg.kubeconfig}")
        # Inject env so subprocess sees it; we can't use _run_kubectl from the
        # server because that reads active_env which isn't set here.
        import subprocess
        env = dict(os.environ)
        env["KUBECONFIG"] = cfg.kubeconfig
        res = subprocess.run(
            ["kubectl", "get", "namespaces", "-o", "name"],
            capture_output=True, text=True, timeout=15, env=env, check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"kubectl failed: {res.stderr.strip()}")
        namespaces = [n.split("/", 1)[-1] for n in res.stdout.splitlines()]
        ns_present = cfg.k8s_namespace in namespaces
        marker = "present ✓" if ns_present else "NOT FOUND ✗"
        return f"kubectl OK · {len(namespaces)} namespaces visible · {cfg.k8s_namespace} namespace {marker}"
    return fn


# ---- runner ----


def main():
    from apps.api.settings import get_settings
    s = get_settings()

    print(_gray("Sherlock preflight — validates every external dependency."))
    print(_gray(f"Configured envs: {', '.join(s.configured_envs())}"))
    print(_gray("Total estimated API spend for this run: < $0.001"))
    print()

    results: list[tuple[str, str]] = []

    # Env-agnostic checks
    for name, cost_hint, fn in [
        ("Postgres + pgvector",  "$0",       check_pgvector),
        ("OpenAI embeddings",    "~$0.0001", check_openai),
        ("Anthropic Haiku 4.5",  "~$0.0005", check_anthropic_haiku),
        ("Datadog (optional)",   "$0",       check_datadog),
    ]:
        print(f"  {name:<32} ", end="", flush=True)
        status, detail, duration = _check(fn)
        badge = _green("OK  ") if status == "ok" else (_yellow("SKIP") if status == "skip" else _red("FAIL"))
        print(f"{badge} {duration:>6.0f}ms  {_gray(cost_hint):<10}  {detail}")
        results.append((name, status))

    # Per-env checks
    for env in s.configured_envs():
        cfg = s.env_config(env)
        print()
        print(_gray(f"--- env: {env} ---"))
        for name, fn in [
            (f"PostgreSQL ({cfg.pg_database or '?'})", _mk_postgres(cfg)),
            (f"Cosmos ({cfg.cosmos_database or '?'})", _mk_cosmos(cfg)),
            (f"Redis", _mk_redis(cfg)),
            (f"kubectl ({cfg.k8s_namespace or '?'} ns)", _mk_kubectl(cfg)),
        ]:
            label = f"  {env}/{name}"
            print(f"  {label:<32} ", end="", flush=True)
            status, detail, duration = _check(fn)
            badge = _green("OK  ") if status == "ok" else (_yellow("SKIP") if status == "skip" else _red("FAIL"))
            print(f"{badge} {duration:>6.0f}ms  {_gray('$0'):<10}  {detail}")
            results.append((label.strip(), status))

    print()
    failures = [n for n, st in results if st == "fail"]
    if failures:
        print(_red(f"✗ {len(failures)} failure(s): {', '.join(failures)}"))
        print(_red("Fix these before running the indexer (or before relying on the failing env)."))
        sys.exit(1)

    skips = [n for n, st in results if st == "skip"]
    if skips:
        print(_yellow(f"⚠ {len(skips)} skipped (likely missing creds): {', '.join(skips)}"))
        print(_yellow("These will gracefully degrade in Sherlock — the agent just won't use them."))

    print(_green(f"✓ all configured checks passed"))


if __name__ == "__main__":
    main()
