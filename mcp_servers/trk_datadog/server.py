"""trk-datadog MCP server.

Log search fallback (Datadog being decommissioned at Trackonomy ~1 week from
hackathon). Used by the RCA agent only when kubectl logs don't reach far
enough back in time. Read-only via Datadog's read scopes.

Tools:
- `search_logs(query, from_ts?, to_ts?, limit?)`
- `trace_correlation(correlation_id, env?, from_ts?, to_ts?)` — find all logs sharing a correlation_id
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


server = Server("trk-datadog")


def _config():
    """Build a Datadog API config. Imported lazily so missing keys don't break import."""
    from datadog_api_client import Configuration
    s = get_settings()
    cfg = Configuration()
    cfg.api_key["apiKeyAuth"] = s.datadog_api_key
    cfg.api_key["appKeyAuth"] = s.datadog_app_key
    cfg.server_variables["site"] = s.datadog_site
    return cfg


def _parse_time(t: str | None, fallback_minutes: int = 60) -> datetime:
    if not t:
        return datetime.now(timezone.utc) - timedelta(minutes=fallback_minutes)
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(minutes=fallback_minutes)


def _do_search(query: str, frm: datetime, to: datetime, limit: int) -> list[dict]:
    from datadog_api_client import ApiClient
    from datadog_api_client.v2.api.logs_api import LogsApi
    from datadog_api_client.v2.model.logs_list_request import LogsListRequest
    from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
    from datadog_api_client.v2.model.logs_sort import LogsSort

    with ApiClient(_config()) as api_client:
        api = LogsApi(api_client)
        body = LogsListRequest(
            filter=LogsQueryFilter(query=query, _from=frm.isoformat(), to=to.isoformat()),
            sort=LogsSort.TIMESTAMP_ASCENDING,
            page={"limit": min(limit, 100)},
        )
        resp = api.list_logs(body=body)
        out: list[dict] = []
        for ev in (resp.data or []):
            attrs = ev.attributes
            attr_dict = attrs.attributes or {}
            out.append({
                "ts": str(attrs.timestamp),
                "service": attrs.service,
                "level": attr_dict.get("status"),
                "message": attrs.message,
                "correlation_id": attr_dict.get("correlation_id"),
                "device_id": attr_dict.get("device_id") or attr_dict.get("tape_id"),
            })
        return out


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_logs",
            description="Search Datadog logs by query string. Up to 100 entries. Default time window: last 60 min.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Datadog log search query syntax"},
                    "from_ts": {"type": "string", "description": "ISO-8601 UTC; default: 1 hour ago"},
                    "to_ts": {"type": "string", "description": "ISO-8601 UTC; default: now"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="trace_correlation",
            description="Find all logs across all services sharing a correlation_id (cross-service trace).",
            inputSchema={
                "type": "object",
                "properties": {
                    "correlation_id": {"type": "string"},
                    "env": {"type": "string", "default": "ppe"},
                    "from_ts": {"type": "string"},
                    "to_ts": {"type": "string"},
                },
                "required": ["correlation_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    s = get_settings()
    if not s.datadog_api_key or not s.datadog_app_key:
        return [TextContent(type="text", text="datadog not configured (missing API/app key)")]

    try:
        if name == "search_logs":
            frm = _parse_time(arguments.get("from_ts"), 60)
            to = _parse_time(arguments.get("to_ts"), 0)
            results = _do_search(arguments["query"], frm, to, arguments.get("limit", 50))
            return [TextContent(type="text", text=json.dumps(results, default=str, indent=2))]

        if name == "trace_correlation":
            env = arguments.get("env", "ppe")
            corr = arguments["correlation_id"]
            q = f"env:{env} @correlation_id:{corr}"
            frm = _parse_time(arguments.get("from_ts"), 120)
            to = _parse_time(arguments.get("to_ts"), 0)
            results = _do_search(q, frm, to, 100)
            return [TextContent(type="text", text=json.dumps(results, default=str, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"datadog error: {type(e).__name__}: {e}")]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
