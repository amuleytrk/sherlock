"""trk-cosmos MCP server.

Read documents from Cosmos containers. Two operations:
- `read_document(container, partition_key, id)` — point read by PK + id
- `query_documents(container, query, parameters?, max_items?)` — SQL-API SELECT

Read-only enforced at two layers: read-only access key, AND we explicitly
reject any query that isn't a SELECT or contains DML keywords.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from azure.cosmos import CosmosClient

from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


server = Server("trk-cosmos")


_CONTAINERS = ["consumables", "infrastructure", "booking", "health", "inventory"]

_FORBIDDEN_KEYWORDS = ("INSERT", "REPLACE", "UPSERT", "DELETE", "MERGE", "EXEC", "EXECUTE")


def _client() -> CosmosClient:
    s = get_settings()
    return CosmosClient(s.cosmos_ppe_endpoint, credential=s.cosmos_ppe_key)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_document",
            description=(
                "Read a single Cosmos document by container, partition_key (list of strings, "
                "in the order Cosmos expects), and document id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string", "enum": _CONTAINERS},
                    "partition_key": {"type": "array", "items": {"type": "string"}},
                    "id": {"type": "string"},
                },
                "required": ["container", "partition_key", "id"],
            },
        ),
        Tool(
            name="query_documents",
            description=(
                "Run a parameterized SELECT against a Cosmos container. "
                "DML keywords (INSERT/REPLACE/UPSERT/DELETE/MERGE/EXEC) are rejected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string", "enum": _CONTAINERS},
                    "query": {"type": "string", "description": "Cosmos SQL-API query (SELECT only)"},
                    "parameters": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Named params: [{name: '@x', value: ...}, ...]",
                    },
                    "max_items": {"type": "integer", "default": 10},
                },
                "required": ["container", "query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    s = get_settings()
    if not s.cosmos_ppe_endpoint or not s.cosmos_ppe_key:
        return [TextContent(type="text", text="cosmos not configured (missing endpoint/key in env)")]

    container_name = arguments.get("container")
    if container_name not in _CONTAINERS:
        return [TextContent(type="text", text=f"unknown container: {container_name}")]

    db = _client().get_database_client(s.cosmos_ppe_database)
    container = db.get_container_client(container_name)

    if name == "read_document":
        try:
            doc = container.read_item(item=arguments["id"], partition_key=arguments["partition_key"])
            return [TextContent(type="text", text=json.dumps(doc, default=str, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"read error: {type(e).__name__}: {e}")]

    if name == "query_documents":
        q = arguments["query"]
        q_norm = re.sub(r"\s+", " ", q.strip()).upper()
        if not q_norm.startswith("SELECT"):
            return [TextContent(type="text", text="only SELECT queries are allowed")]
        for forbidden in _FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{forbidden}\b", q_norm):
                return [TextContent(type="text", text=f"forbidden keyword: {forbidden}")]
        try:
            params = arguments.get("parameters", []) or []
            max_items = arguments.get("max_items", 10)
            items = list(
                container.query_items(
                    query=q,
                    parameters=params,
                    enable_cross_partition_query=True,
                    max_item_count=max_items,
                )
            )[:max_items]
            return [TextContent(type="text", text=json.dumps(items, default=str, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"query error: {type(e).__name__}: {e}")]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
