"""sherlock-rag MCP server.

Hybrid search over the indexed corpus in pgvector. Combines dense vector
similarity (text-embedding-3-large via OpenAI, cosine distance over halfvec)
with PostgreSQL tsvector keyword search, fused via Reciprocal Rank Fusion in
SQL CTEs.

Tools:
- `search(query, service?, category?, top_k?)` → top-k chunks with citations
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector

from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


server = Server("sherlock-rag")


# Note: the embedding column is `vector(3072)`, but the HNSW index is on the
# `halfvec(3072)` cast (pgvector caps vector HNSW at 2000 dims). For ANN to
# use the index we cast both sides to halfvec at query time.
HYBRID_SQL = """
WITH dense AS (
    SELECT chunk_id, content, file_path, line_start, line_end, service, category,
           heading_hierarchy, parent_id,
           ROW_NUMBER() OVER (
               ORDER BY embedding::halfvec(3072) <=> %(qvec)s::halfvec(3072)
           ) AS rank_dense
    FROM vector_store.chunks
    WHERE release = %(release)s
      AND (%(service_filter)s::text IS NULL OR service = %(service_filter)s)
      AND (%(category_filter)s::text IS NULL OR category = %(category_filter)s)
      AND (%(system_filter)s::text IS NULL OR system IN (%(system_filter)s, 'both'))
    ORDER BY embedding::halfvec(3072) <=> %(qvec)s::halfvec(3072)
    LIMIT 30
),
fulltext AS (
    SELECT chunk_id, content, file_path, line_start, line_end, service, category,
           heading_hierarchy, parent_id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank_text
    FROM vector_store.chunks, plainto_tsquery('english', %(qtext)s) q
    WHERE release = %(release)s
      AND (%(service_filter)s::text IS NULL OR service = %(service_filter)s)
      AND (%(category_filter)s::text IS NULL OR category = %(category_filter)s)
      AND (%(system_filter)s::text IS NULL OR system IN (%(system_filter)s, 'both'))
      AND tsv @@ q
    ORDER BY ts_rank_cd(tsv, q) DESC
    LIMIT 30
),
fused AS (
    SELECT COALESCE(d.chunk_id, f.chunk_id) AS chunk_id,
           COALESCE(d.content, f.content) AS content,
           COALESCE(d.file_path, f.file_path) AS file_path,
           COALESCE(d.line_start, f.line_start) AS line_start,
           COALESCE(d.line_end, f.line_end) AS line_end,
           COALESCE(d.service, f.service) AS service,
           COALESCE(d.category, f.category) AS category,
           COALESCE(d.heading_hierarchy, f.heading_hierarchy) AS heading_hierarchy,
           COALESCE(d.parent_id, f.parent_id) AS parent_id,
           COALESCE(1.0/(60+d.rank_dense), 0.0)
           + COALESCE(1.0/(60+f.rank_text), 0.0) AS score
    FROM dense d FULL OUTER JOIN fulltext f USING (chunk_id)
)
SELECT chunk_id, content, file_path, line_start, line_end, service, category,
       heading_hierarchy, parent_id, score
FROM fused
ORDER BY score DESC
LIMIT %(top_k)s;
"""


def _embed(text: str) -> list[float]:
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key)
    resp = client.embeddings.create(
        model="text-embedding-3-large", input=[text], dimensions=3072,
    )
    return resp.data[0].embedding


def hybrid_search(query: str, *, service: str | None = None,
                  category: str | None = None, top_k: int = 20,
                  system: str | None = None) -> list[dict]:
    """Run hybrid search; returns list of chunk dicts ordered by RRF score.

    `system` filters by the chunk's database-system tag. `mssql` returns chunks
    tagged 'mssql' OR 'both'; `postgres` returns 'postgres' OR 'both'. None
    skips the filter entirely (returns everything)."""
    s = get_settings()
    qvec = _embed(query)
    params = {
        "qvec": qvec,
        "qtext": query,
        "release": s.sherlock_release,
        "service_filter": service,
        "category_filter": category,
        "system_filter": system,
        "top_k": top_k,
    }
    with psycopg.connect(s.database_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(HYBRID_SQL, params)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search",
            description=(
                "Hybrid (dense + keyword) search over the indexed corpus. "
                "Returns chunks with file_path:line_range citations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "service": {"type": "string", "description": "Optional service filter"},
                    "category": {"type": "string", "description": "Optional category filter"},
                    "top_k": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "search":
        return [TextContent(type="text", text=f"unknown tool: {name}")]
    # When called from the RCA agent, the active_system contextvar drives the
    # filter implicitly so the agent doesn't need to thread it through args.
    from apps.api.env_context import active_system
    try:
        results = hybrid_search(
            arguments["query"],
            service=arguments.get("service"),
            category=arguments.get("category"),
            top_k=arguments.get("top_k", 20),
            system=arguments.get("system") or active_system.get() or None,
        )
        return [TextContent(type="text", text=json.dumps(results, default=str, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=f"search error: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
