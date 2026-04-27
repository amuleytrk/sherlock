"""pgvector schema deployment + connection helpers.

Schema lives in `vector_store.chunks`. One row per indexed chunk, with a
3072-dim dense embedding (text-embedding-3-large) and an auto-generated
tsvector for keyword search. HNSW for ANN; GIN for full-text.
"""
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

from apps.api.settings import get_settings


SCHEMA_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS vector_store;

CREATE TABLE IF NOT EXISTS vector_store.chunks (
    chunk_id          TEXT PRIMARY KEY,
    release           TEXT NOT NULL,
    service           TEXT NOT NULL,
    category          TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    line_start        INTEGER,
    line_end          INTEGER,
    heading_hierarchy TEXT[],
    http_method       TEXT,
    endpoint_path     TEXT,
    middleware        TEXT[],
    context           TEXT,
    content           TEXT NOT NULL,
    embedding         vector(3072) NOT NULL,
    tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    parent_id         TEXT REFERENCES vector_store.chunks(chunk_id),
    last_modified     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Note: pgvector's HNSW index doesn't support 3072-d vectors directly
-- (max 2000 dims for HNSW). For 3072 we have two options:
--   1. Use IVFFlat instead (supports any dim) — slower but simpler
--   2. Use halfvec (16-bit floats) which raises HNSW dim limit to 4000
-- We pick option 2: cast embedding to halfvec at index time.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON vector_store.chunks USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
    WITH (m=16, ef_construction=200);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv
    ON vector_store.chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS idx_chunks_release
    ON vector_store.chunks (release);

CREATE INDEX IF NOT EXISTS idx_chunks_service
    ON vector_store.chunks (service);

CREATE INDEX IF NOT EXISTS idx_chunks_category
    ON vector_store.chunks (category);

CREATE INDEX IF NOT EXISTS idx_chunks_release_service
    ON vector_store.chunks (release, service);
"""


def get_conn() -> psycopg.Connection:
    s = get_settings()
    conn = psycopg.connect(s.database_url)
    register_vector(conn)
    return conn


def deploy_schema() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
        conn.commit()
    print("schema deployed")


if __name__ == "__main__":
    deploy_schema()
