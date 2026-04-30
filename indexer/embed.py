"""Embed chunks via OpenAI batch API and upsert into pgvector.

Batched in groups of 64 chunks per OpenAI call (well within their limits).
Upsert uses ON CONFLICT (chunk_id) so re-running the indexer is idempotent.
"""
from __future__ import annotations

from typing import Iterable

import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector

from apps.api.settings import get_settings
from indexer.chunk import Chunk


_BATCH_SIZE = 64
_MODEL = "text-embedding-3-large"
_DIMS = 3072


def _client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.openai_api_key)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _client().embeddings.create(model=_MODEL, input=texts, dimensions=_DIMS)
    return [d.embedding for d in resp.data]


UPSERT_SQL = """
INSERT INTO vector_store.chunks (
    chunk_id, release, service, category, system, file_path,
    line_start, line_end, heading_hierarchy,
    http_method, endpoint_path, middleware,
    context, content, embedding, parent_id, last_modified
)
VALUES (
    %(chunk_id)s, %(release)s, %(service)s, %(category)s, %(system)s, %(file_path)s,
    %(line_start)s, %(line_end)s, %(heading_hierarchy)s,
    %(http_method)s, %(endpoint_path)s, %(middleware)s,
    %(context)s, %(content)s, %(embedding)s, %(parent_id)s, NOW()
)
ON CONFLICT (chunk_id) DO UPDATE SET
    release = EXCLUDED.release,
    service = EXCLUDED.service,
    category = EXCLUDED.category,
    system = EXCLUDED.system,
    file_path = EXCLUDED.file_path,
    line_start = EXCLUDED.line_start,
    line_end = EXCLUDED.line_end,
    heading_hierarchy = EXCLUDED.heading_hierarchy,
    http_method = EXCLUDED.http_method,
    endpoint_path = EXCLUDED.endpoint_path,
    middleware = EXCLUDED.middleware,
    context = EXCLUDED.context,
    content = EXCLUDED.content,
    embedding = EXCLUDED.embedding,
    parent_id = EXCLUDED.parent_id,
    last_modified = NOW();
"""


def upsert_chunks(chunks: Iterable[Chunk], verbose: bool = True) -> int:
    chunks = list(chunks)
    if not chunks:
        return 0
    s = get_settings()
    n = 0
    with psycopg.connect(s.database_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            for i in range(0, len(chunks), _BATCH_SIZE):
                batch = chunks[i:i + _BATCH_SIZE]
                vectors = embed_texts([c.content for c in batch])
                for c, vec in zip(batch, vectors):
                    cur.execute(
                        UPSERT_SQL,
                        {
                            "chunk_id": c.chunk_id,
                            "release": c.release,
                            "service": c.service,
                            "category": c.category,
                            "system": c.system,
                            "file_path": c.file_path,
                            "line_start": c.line_start,
                            "line_end": c.line_end,
                            "heading_hierarchy": c.heading_hierarchy,
                            "http_method": None,
                            "endpoint_path": None,
                            "middleware": None,
                            "context": None,
                            "content": c.content,
                            "embedding": vec,
                            "parent_id": c.parent_id,
                        },
                    )
                    n += 1
                conn.commit()
                if verbose:
                    print(f"  upserted {n}/{len(chunks)}")
    return n
