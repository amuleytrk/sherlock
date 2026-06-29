"""trk-postgres MCP server.

Read-only parameterized SELECT queries against the trk PostgreSQL schema
(PG 18, Azure Flexible Server).  Reads creds from the active env's config
(PPE / Stage / future envs) — see env_context.py.

Read-only is enforced at three layers:
1. Connection-level: options="-c default_transaction_read_only=on"
2. Template-only: no arbitrary SQL accepted
3. SQL user permission (cred must be SELECT-only or the above blocks writes)

UUID derivation: tenant-scoped templates that need account_id/application_id
accept legacy params (customer_id, authorized_group, optionally application_code)
and derive the UUID via mcp_servers.trk_postgres.uuids before binding.

Primary device lookup key: device_id (unique index udx_device_device_id).
qrcode is a documented fallback via duplicate_check or device_by_qrcode.
"""
from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any

import psycopg

from apps.api.env_context import EnvCreds, active_env
from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_servers.trk_postgres.templates import CATALOG
from mcp_servers.trk_postgres.uuids import account_uuid, application_uuid


server = Server("trk-postgres")

_DEFAULT_LIMIT = 20
_DEFAULT_YEAR = datetime.datetime.now(datetime.timezone.utc).year


def build_connect_kwargs(cfg: EnvCreds) -> dict:
    """Return psycopg.connect() kwargs for *cfg* (testable without a live DB).

    Read-only is enforced via the options string — even if the DB cred is
    read-write, every connection opened through this function is blocked from
    writing by the session-level GUC.
    """
    options = (
        f"-c default_transaction_read_only=on "
        f"-c search_path={cfg.pg_search_path}"
    )
    return {
        "host": cfg.pg_host,
        "port": cfg.pg_port,
        "dbname": cfg.pg_database,
        "user": cfg.pg_user,
        "password": cfg.pg_password,
        "sslmode": cfg.pg_sslmode,
        "options": options,
        "connect_timeout": 10,
        # row_factory set at connection open time (not a connect kwarg)
    }


def _connect():
    s = get_settings()
    cfg = s.env_config(active_env.get() or s.sherlock_default_env)
    if not (cfg.pg_host and cfg.pg_user and cfg.pg_password):
        raise RuntimeError(
            f"PostgreSQL not configured for env={cfg.env!r} — set "
            f"PG_{cfg.env.upper()}_HOST / _USER / _PASSWORD / _DATABASE in .env"
        )
    kwargs = build_connect_kwargs(cfg)
    conn = psycopg.connect(**kwargs, row_factory=psycopg.rows.dict_row)
    return conn


def _resolve_params(query_type: str, raw_params: dict) -> dict:
    """Apply defaults and UUID derivation to raw params before binding.

    - Fills year / limit defaults for templates that use them.
    - If a template needs account_id and it's not supplied, derives it from
      customer_id + authorized_group via uuids.account_uuid().
    - Same for application_id: derives from customer_id + authorized_group +
      application_code.
    """
    params = dict(raw_params)

    # Year / limit defaults (used by device_event queries)
    if "year" in _year_limit_templates(query_type):
        params.setdefault("year", _DEFAULT_YEAR)
    if "limit" in _limit_templates(query_type):
        params.setdefault("limit", _DEFAULT_LIMIT)

    # UUID derivation helpers — accept legacy (customer_id, authorized_group)
    # and silently derive account_id if not directly provided.
    tmpl = CATALOG[query_type]
    sql = tmpl["sql"]

    if "%(account_id)s" in sql and "account_id" not in params:
        cid = params.get("customer_id", "")
        ag = params.get("authorized_group", "")
        if cid and ag:
            params["account_id"] = account_uuid(cid, ag)

    if "%(application_id)s" in sql and "application_id" not in params:
        cid = params.get("customer_id", "")
        ag = params.get("authorized_group", "")
        code = params.get("application_code", "")
        if cid and ag and code:
            params["application_id"] = application_uuid(cid, ag, code)

    return params


def _year_limit_templates(query_type: str) -> list[str]:
    """Templates that have a 'year' optional param."""
    tmpl = CATALOG.get(query_type, {})
    return tmpl.get("optional", []) + tmpl.get("required", [])


def _limit_templates(query_type: str) -> list[str]:
    """Templates that have a 'limit' optional param."""
    tmpl = CATALOG.get(query_type, {})
    return tmpl.get("optional", []) + tmpl.get("required", [])


@server.list_tools()
async def list_tools() -> list[Tool]:
    catalog_doc = "\n".join(
        f"  - {name}(required={spec['required']}, optional={spec['optional']}): {spec['description']}"
        for name, spec in sorted(CATALOG.items())
    )
    return [
        Tool(
            name="query_template",
            description=(
                "Run a vetted, read-only parameterized SELECT against the trk "
                "PostgreSQL schema in the currently active env (PPE/Stage/etc).\n"
                "Available query_types:\n"
                f"{catalog_doc}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": list(CATALOG.keys()),
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Named params required by the chosen query_type. "
                            "UUID derivation: for tenant-scoped queries you may "
                            "supply customer_id + authorized_group instead of "
                            "account_id, and optionally application_code instead "
                            "of application_id — the server derives the UUID."
                        ),
                    },
                },
                "required": ["query_type"],
            },
        ),
        Tool(
            name="list_query_types",
            description="List all available PostgreSQL query_types and their parameter signatures.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "list_query_types":
        catalog = {
            qt: {
                "required": spec["required"],
                "optional": spec["optional"],
                "description": spec["description"],
            }
            for qt, spec in CATALOG.items()
        }
        return [TextContent(type="text", text=json.dumps(catalog, indent=2))]

    if name != "query_template":
        return [TextContent(type="text", text=f"unknown tool: {name}")]

    qt = arguments.get("query_type")
    if qt not in CATALOG:
        return [TextContent(type="text", text=f"unknown query_type: {qt!r}")]

    spec = CATALOG[qt]
    raw_params = arguments.get("params", {}) or {}

    # Check required params (before UUID derivation — derivation fills account_id
    # when customer_id+authorized_group are supplied in lieu of it)
    missing = [
        p for p in spec["required"]
        if p not in raw_params
        # Allow account_id to be derived from customer_id + authorized_group
        and not (
            p == "account_id"
            and "customer_id" in raw_params
            and "authorized_group" in raw_params
        )
        # Allow application_id to be derived from customer_id + authorized_group + application_code
        and not (
            p == "application_id"
            and "customer_id" in raw_params
            and "authorized_group" in raw_params
            and "application_code" in raw_params
        )
    ]
    if missing:
        return [TextContent(type="text", text=f"missing required params: {missing}")]

    params = _resolve_params(qt, raw_params)

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(spec["sql"], params)
                rows = cur.fetchall()
        return [TextContent(type="text", text=json.dumps(rows, default=str, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"query error: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
