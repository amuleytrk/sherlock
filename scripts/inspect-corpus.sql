-- =============================================================================
-- Sherlock — corpus inspection queries
-- =============================================================================
-- Run these against the local pgvector Postgres set up by `docker compose up`.
-- Connection: postgres://sherlock:sherlock_local_dev@localhost:5433/sherlock
--
-- In DBeaver: open this file, place cursor anywhere in a query, hit Cmd+Enter
--   (or Ctrl+Enter on Linux/Win) to execute that single query block.
--
-- Schema:  vector_store.chunks
-- Columns: chunk_id, release, service, category, file_path,
--          line_start, line_end, heading_hierarchy (text[]),
--          http_method, endpoint_path, middleware (text[]),
--          context, content, embedding (vector 3072), tsv (tsvector),
--          parent_id, last_modified, created_at
--
-- Tip: avoid `SELECT *` — the embedding column is 3072 floats and will
-- thrash the result grid. The queries below all project explicit columns.
-- =============================================================================


-- ---- 1. SANITY CHECKS --------------------------------------------------------

-- 1a. Total chunk count per release tag.
SELECT release, count(*) AS chunks
FROM vector_store.chunks
GROUP BY release
ORDER BY chunks DESC;


-- 1b. Coverage by service + category. The shape of this result is the first
--     thing to verify after running the indexer — if a service is missing
--     here, the indexer crawler didn't find anything indexable for it.
SELECT service, category, count(*) AS chunks
FROM vector_store.chunks
GROUP BY service, category
ORDER BY service, chunks DESC;


-- 1c. Total storage size of the corpus (text + vectors + indices).
SELECT
    pg_size_pretty(pg_total_relation_size('vector_store.chunks')) AS total,
    pg_size_pretty(pg_relation_size('vector_store.chunks')) AS heap,
    pg_size_pretty(pg_indexes_size('vector_store.chunks')) AS indexes;


-- 1d. Per-index size (HNSW for vectors is the heaviest).
SELECT
    indexrelid::regclass AS index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_index
WHERE indrelid = 'vector_store.chunks'::regclass
ORDER BY pg_relation_size(indexrelid) DESC;


-- ---- 2. CONTENT BROWSING -----------------------------------------------------

-- 2a. Browse chunks for a specific service (sorted by file).
--     Edit the WHERE clause to pick the service you care about.
SELECT
    file_path,
    line_start,
    line_end,
    array_to_string(heading_hierarchy, ' › ') AS heading,
    length(content) AS bytes,
    chunk_id
FROM vector_store.chunks
WHERE service = 'multi-tenant-core-services'
ORDER BY file_path, line_start
LIMIT 200;


-- 2b. Look at a single chunk's full content. Paste a chunk_id from the
--     query above (or anywhere else) into the WHERE clause.
SELECT
    file_path,
    line_start || '–' || line_end AS lines,
    array_to_string(heading_hierarchy, ' › ') AS heading,
    content
FROM vector_store.chunks
WHERE chunk_id = '__paste_chunk_id_here__';


-- 2c. All chunks for a single file (great when you suspect chunking went weird).
SELECT
    line_start,
    line_end,
    array_to_string(heading_hierarchy, ' › ') AS heading,
    length(content) AS bytes,
    chunk_id
FROM vector_store.chunks
WHERE file_path LIKE '%/IngressController.js'  -- edit me
ORDER BY line_start;


-- ---- 3. KEYWORD SEARCH (tsvector) -------------------------------------------

-- 3a. Find chunks mentioning specific identifiers — useful for sanity-
--     checking that the things you ASK Sherlock about are actually
--     reachable in the index.
SELECT
    file_path,
    line_start,
    line_end,
    service,
    ts_rank_cd(tsv, q) AS rank,
    chunk_id
FROM vector_store.chunks,
     plainto_tsquery('english', 'lookup_parcels device_status') q  -- edit me
WHERE tsv @@ q
ORDER BY rank DESC
LIMIT 25;


-- 3b. Same as 3a but only within service architecture docs (CLAUDE.md +
--     systemFlow.md). Often the highest-quality answers come from these.
SELECT
    file_path,
    line_start,
    line_end,
    service,
    array_to_string(heading_hierarchy, ' › ') AS heading,
    ts_rank_cd(tsv, q) AS rank
FROM vector_store.chunks,
     plainto_tsquery('english', 'lime selection algorithm') q  -- edit me
WHERE tsv @@ q
  AND category IN ('architecture', 'service_architecture')
ORDER BY rank DESC
LIMIT 15;


-- 3c. How many chunks contain a given identifier?
SELECT count(*) AS chunks_with_term
FROM vector_store.chunks
WHERE tsv @@ plainto_tsquery('english', 'cross_customer_mesh_allowed');  -- edit me


-- ---- 4. PARENT-CHILD HIERARCHY (markdown docs) -------------------------------

-- 4a. How many parent-child pairs do we have? Markdown docs with deep
--     heading hierarchies link children to parents so the agent can fetch
--     the parent's lead-in text alongside a matched child chunk.
SELECT
    count(*) AS total_chunks,
    count(parent_id) AS chunks_with_parent,
    count(*) FILTER (WHERE chunk_id IN (SELECT parent_id FROM vector_store.chunks)) AS chunks_that_are_parents;


-- 4b. Walk a parent → its children for systemFlow.md.
SELECT
    line_start,
    line_end,
    array_to_string(heading_hierarchy, ' › ') AS heading,
    chunk_id,
    parent_id
FROM vector_store.chunks
WHERE file_path LIKE '%systemFlow.md'
ORDER BY line_start;


-- ---- 5. ANOMALY HUNTING ------------------------------------------------------

-- 5a. Smallest chunks (often a sign of degenerate parsing — e.g. a heading
--     with no body text).
SELECT
    service,
    file_path,
    line_start,
    line_end,
    length(content) AS bytes,
    LEFT(content, 80) AS preview
FROM vector_store.chunks
WHERE length(content) < 60
ORDER BY length(content) ASC
LIMIT 20;


-- 5b. Largest chunks (usually a single oversized markdown section or a
--     truncated controller method).
SELECT
    service,
    file_path,
    line_start,
    line_end,
    length(content) AS bytes
FROM vector_store.chunks
ORDER BY length(content) DESC
LIMIT 10;


-- 5c. Files with the most chunks (often the most-fragmented files —
--     useful for spotting whether a file's chunking matches your intuition).
SELECT
    file_path,
    count(*) AS chunks,
    sum(length(content)) AS total_bytes
FROM vector_store.chunks
GROUP BY file_path
ORDER BY chunks DESC
LIMIT 25;


-- 5d. Categories we found vs categories we expected. If 'frontend_component'
--     is zero, the dashboard repo's classifier didn't match any files.
SELECT category, count(*)
FROM vector_store.chunks
GROUP BY category
ORDER BY count(*) DESC;


-- ---- 6. INDEXING ACTIVITY ----------------------------------------------------

-- 6a. When was each file last indexed? Useful when you re-run the indexer
--     after a release rolls and want to confirm changes propagated.
SELECT
    service,
    file_path,
    max(last_modified) AS most_recent_index,
    count(*) AS chunks
FROM vector_store.chunks
GROUP BY service, file_path
ORDER BY most_recent_index DESC
LIMIT 30;


-- 6b. Indexing throughput — chunks added per minute over the most recent run.
SELECT
    date_trunc('minute', created_at) AS minute,
    count(*) AS chunks
FROM vector_store.chunks
WHERE created_at > now() - interval '2 hours'
GROUP BY minute
ORDER BY minute DESC;


-- ---- 7. NICE-TO-HAVE: API ROUTE INVENTORY ------------------------------------
-- These columns are populated for chunks the indexer classified as api_route
-- (currently empty in v1 — the route-extraction enrichment is a TODO; left
-- here so the queries are ready when that lands).

-- 7a. List every API route Sherlock has indexed.
SELECT
    service,
    http_method,
    endpoint_path,
    array_to_string(middleware, ', ') AS middleware,
    file_path,
    line_start
FROM vector_store.chunks
WHERE category = 'api_route'
  AND endpoint_path IS NOT NULL
ORDER BY service, endpoint_path;


-- 7b. APIs by service.
SELECT
    service,
    count(*) AS routes
FROM vector_store.chunks
WHERE category = 'api_route'
GROUP BY service
ORDER BY routes DESC;


-- ---- 8. DESTRUCTIVE — uncomment ONLY if you intend to wipe ------------------
-- TRUNCATE vector_store.chunks;       -- nuke the corpus, keep the schema
-- DROP SCHEMA vector_store CASCADE;   -- nuke everything; redeploy with `uv run python -m indexer.db`
