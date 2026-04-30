"""trk-mssql MCP server.

Read-only parameterized SELECT queries against the trk schema. Reads creds
from the active env's config (PPE / Stage / future envs) — see env_context.py.

Read-only is enforced at three layers:
1. SQL user permission (must be SELECT-only on schema::trk)
2. Template-only — no arbitrary SQL accepted
3. (Optional) connection-level read-only flag if the driver supports it
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pymssql

from apps.api.env_context import active_env
from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_servers.trk_mssql.templates import QUERY_TEMPLATES


server = Server("trk-mssql")


def _connect():
    s = get_settings()
    cfg = s.env_config(active_env.get() or s.sherlock_default_env)
    if not (cfg.mssql_server and cfg.mssql_user and cfg.mssql_password):
        raise RuntimeError(
            f"MSSQL not configured for env={cfg.env!r} — set "
            f"MSSQL_{cfg.env.upper()}_SERVER / _USER / _PASSWORD / _DATABASE in .env"
        )
    return pymssql.connect(
        server=cfg.mssql_server,
        user=cfg.mssql_user,
        password=cfg.mssql_password,
        database=cfg.mssql_database,
        login_timeout=10,
        timeout=20,
        as_dict=True,
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    catalog_doc = "\n".join(
        f"  - {name}({', '.join(spec['params'])}): {spec['doc']}"
        for name, spec in sorted(QUERY_TEMPLATES.items())
    )
    return [
        Tool(
            name="query_template",
            description=(
                "Run a vetted, read-only parameterized SELECT against the trk schema "
                "in the currently active env (PPE/Stage/etc).\n"
                "Available query_types:\n"
                f"{catalog_doc}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query_type": {"type": "string", "enum": list(QUERY_TEMPLATES.keys())},
                    "params": {
                        "type": "object",
                        "description": "Named params required by the chosen query_type",
                    },
                },
                "required": ["query_type"],
            },
        ),
        Tool(
            name="list_query_types",
            description="List all available query_types and their parameter signatures.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "list_query_types":
        catalog = {
            qt: {"params": spec["params"], "doc": spec["doc"]}
            for qt, spec in QUERY_TEMPLATES.items()
        }
        return [TextContent(type="text", text=json.dumps(catalog, indent=2))]

    if name != "query_template":
        return [TextContent(type="text", text=f"unknown tool: {name}")]

    qt = arguments.get("query_type")
    if qt not in QUERY_TEMPLATES:
        return [TextContent(type="text", text=f"unknown query_type: {qt}")]

    spec = QUERY_TEMPLATES[qt]
    params = arguments.get("params", {}) or {}
    missing = [p for p in spec["params"] if p not in params]
    if missing:
        return [TextContent(type="text", text=f"missing required params: {missing}")]

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
