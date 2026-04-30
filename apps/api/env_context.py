"""Per-request active environment plumbing.

Sherlock supports multiple environments (PPE, Stage, eventually Prod). Adding a
new env should require only `.env` changes — no code modifications. The
mechanism:

1. The frontend sends `env: "stage"` in each chat request.
2. `apps/api/main.py` sets `active_env` (a ContextVar) before dispatching to
   any agent or tool.
3. MCP servers (mssql, cosmos, redis, kubectl) read the active env, ask
   `Settings.env_config(active_env.get())` for credentials, and use them.

The credentials themselves come from env vars named `<TOOL>_<ENV>_<FIELD>`
(e.g. `MSSQL_STAGE_SERVER`). `env_config()` does the dynamic lookup so settings
schema doesn't need to grow as envs are added.

Why ContextVar and not a parameter on every tool? FastAPI runs each request in
its own context, so a ContextVar set in the request handler is automatically
scoped to that request — no risk of bleed between concurrent users, and
agents/tools don't need to thread an `env` argument through every call.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class EnvCreds:
    """Credentials and conventions for a single deployment environment.

    Populated by `Settings.env_config(env)` from `<TOOL>_<ENV>_<FIELD>` env
    vars. Empty strings mean "not configured" — MCP servers should surface a
    clear "not available in this env" message rather than crashing.
    """
    env: str

    # MSSQL (per-env subscription/server)
    mssql_server: str = ""
    mssql_database: str = ""
    mssql_user: str = ""
    mssql_password: str = ""

    # Cosmos for Postgres / SQL API (per-env)
    cosmos_endpoint: str = ""
    cosmos_key: str = ""
    cosmos_database: str = ""

    # Azure Redis Cache — accept either a single URL or host/port/key triplet.
    redis_url: str = ""
    redis_host: str = ""
    redis_port: int = 6380
    redis_key: str = ""
    redis_tls: bool = True

    # AKS access — self-contained kubeconfig per env (admin cert or SP-backed).
    # Path is set on the kubectl subprocess via env var, so flipping envs never
    # touches the user's day-to-day kubectl context.
    kubeconfig: str = ""
    # Trackonomy convention: pods live in a per-env namespace and carry an env
    # suffix in their deployment names (e.g. `ingress-service-ppe-deployment`,
    # `ingress-service-dev-deployment`). The kubectl tool uses these to scope
    # `kubectl get pods` / `kubectl logs` queries automatically.
    k8s_namespace: str = ""
    k8s_pod_suffix: str = ""


# Default is empty so a missing setter is a loud bug — settings.env_config()
# falls back to `sherlock_default_env` when called without an explicit env.
active_env: ContextVar[str] = ContextVar("active_env", default="")

# Active database-system filter. Trackonomy is mid-migration MSSQL → PostgreSQL.
# The corpus contains docs from both eras; this contextvar lets retrieval scope
# to one system. Values: "mssql" | "postgres" | "" (no filter, return all).
# Default empty so RCA tool calls work even if upstream forgets to set it.
active_system: ContextVar[str] = ContextVar("active_system", default="")
