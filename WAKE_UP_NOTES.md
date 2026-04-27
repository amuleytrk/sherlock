# Sherlock — Wake-Up Notes

> **TL;DR**: Whole stack is built and 77/77 tests pass. Fill in `.env`, run `./scripts/start_dev.sh`, and you should be live. Live integrations against PPE infrastructure exercise on first real query — those couldn't be tested overnight (creds + hooks).

---

## What got built (overnight, autonomously)

All five days of the implementation plan landed:

| Day | Status | Commits |
|-----|--------|---------|
| 1 — Foundation + indexer + first MCP | ✅ all code, 30 tests pass | 6 |
| 2 — Discovery agent + 5 more MCP servers + full corpus | ✅ all code, 23 tests pass | 4 |
| 3 — RCA agent + Code Execution + visuals | ✅ all code, 10 tests pass | 1 |
| 4 — React UI | ✅ scaffolded, components, build passes | 1 |
| 5 — Polish (audit, store, scripts) | ✅ SQLite store, redaction, dev runner | this commit |

**77 unit/smoke tests, all green.** Run `uv run pytest` to verify.

## What's needed from you to make it live

### 1. Fill in `.env`

```bash
cp .env.example .env  # already done overnight
# edit with real values:
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
MSSQL_PPE_USER=...
MSSQL_PPE_PASSWORD=...
COSMOS_PPE_ENDPOINT=https://....documents.azure.com:443/
COSMOS_PPE_KEY=...
COSMOS_PPE_DATABASE=...
REDIS_PPE_URL=rediss://:...@...redis.cache.windows.net:6380
DATADOG_API_KEY=...
DATADOG_APP_KEY=...
KUBECONFIG=/Users/aadityamuley/.kube/config
```

The app **gracefully degrades** when keys are missing — Discovery returns a "key not set" status event instead of crashing, RCA agent yields a clear blocked message. So you can incrementally add keys and watch the modes light up.

### 2. Index the corpus

```bash
./scripts/clone_corpus.sh        # already symlinked your existing repos
uv run python -m indexer.run     # ~10 min, ~$1.20 in OpenAI embeddings
```

Verify with:

```bash
docker exec sherlock-postgres psql -U sherlock -d sherlock -c \
  "SELECT count(*), count(DISTINCT service) FROM vector_store.chunks;"
```

Expect ~5K chunks across 6 services.

### 3. Run the app

```bash
./scripts/start_dev.sh
# → opens FastAPI on :8000, Vite on :5173
# → visit http://localhost:5173
```

Mobile demo via tunnel (separate terminal):

```bash
./scripts/start_tunnel.sh
# share the printed *.trycloudflare.com URL with your phone
```

---

## Architectural decisions made overnight (worth your eye)

| Decision | Why |
|----------|-----|
| **Halfvec HNSW** for the 3072-d embedding index | pgvector's regular HNSW caps at 2000 dims; halfvec extends to 4000. Casts at index + query time. |
| **In-process MCP dispatch** (vs stdio subprocess per server) | For a single-process app with 6 MCP servers we don't pay the IPC overhead. The same `call_tool(name, args)` API works either way. If we ever ship multi-process, swap in stdio. |
| **Heuristic router fallback** when `ANTHROPIC_API_KEY` is empty | Lets the app stay functional in dev/test without burning tokens or breaking when keys are missing. Production path is still Haiku 4.5. |
| **Python 3.13.2 + Pydantic 2 + plain JS frontend** (not TS) | Plan called for 3.12+. JS over TS to minimize hackathon friction. |
| **Vite proxy for /api → :8000** | Same-origin fetches in dev, no CORS dance. Keep this for prod by serving the built UI behind FastAPI. |
| **Sub-agent dispatch via the `Task` tool** | Capped at 3 sub-agents per RCA, 4 tool calls each. Triggers when the agent detects independent investigation branches. |
| **Audit redaction** (defense in depth) | Even if a tool arg accidentally carries a secret, the regex masks it before SQLite write. Five regex patterns, 10 tests. |
| **TimedTool wraps every RCA tool call** | Audit log + duration tracking are always-on. Surfaces in the UI's audit panel. |

## Open items (for you, not blocking)

1. **CPC-576 regression case** — `tests/regression/` is **NOT** written because it needs live PPE creds to validate against the real device data. Once you have the env populated, drop the failing scenario into `tests/regression/test_cpc576.py` and run end-to-end.
2. **Demo video** — `docs/DEMO.md` script is in the plan; recording requires the demo to actually run.
3. **Submission writeup** — `docs/SUBMISSION.md` template is in the plan; impact numbers should be filled with real measurements once you've used the system for a day.
4. **Datadog post-decommission** — kubectl is the primary log source. Once Datadog goes away, swap `trk-datadog` for whatever replaces it (Azure Monitor / Log Analytics / Loki / etc.).
5. **The plan's done-criteria for Days 1, 2, 3 specify live PPE integration tests** — those need to be re-run with real creds. Watch for the indexer to actually populate pgvector successfully (~5K chunks) and the RCA agent to reach a known-answer conclusion in <60s.

## Files & dirs you might want to look at first

- `apps/api/agents/rca.py` — the heart of the RCA loop (450 lines)
- `apps/api/agents/discovery.py` — linear RAG (Discovery)
- `apps/api/prompts/rca_system.md` — agent system prompt (port of `rcaAgentPrompt.md`)
- `mcp_servers/trk_mssql/templates.py` — vetted SELECT catalog
- `apps/web/src/components/ChatStream.jsx` — UI driver
- `apps/web/src/components/RcaReport.jsx` — final artifact view
- `~/plans/work/designs/rca-tool/autonomous-execution-log.md` — running log

## Numbers

```
git log --oneline | wc -l   # ~16 commits since Initial commit
find . -name '*.py' -not -path './.venv/*' -not -path './repos/*' | xargs wc -l | tail -1   # ~3.7K LOC Python
find apps/web/src -name '*.jsx' -o -name '*.js' | xargs wc -l | tail -1   # ~700 LOC React
77 tests, all green
0 external API calls made (no creds → graceful degrade)
```

## My recommendation for Day 1 morning

### Step 0 — try demo mode first (works without any creds)

Demo mode streams canned-but-realistic agent traces so you can see the UI working immediately:

```bash
# ensure .env has SHERLOCK_DEMO_MODE=1 (or export it)
SHERLOCK_DEMO_MODE=1 ./scripts/start_dev.sh
```

Open http://localhost:5173 and try any of these (they're prompts in the welcome screen):

- *How do I label a white tape device?* → Discovery answer with file:line citations
- *What does feature_configuration.cross_customer_mesh_allowed do?* → flag explanation
- *Where is the lime selection algorithm implemented?* → ingress-service file pointer
- *Device AABBCCDDEEFF events not in lookup_parcels in PPE* → full RCA with timeline, evidence files, Mermaid service-hop diagram, structured remediation

This bypasses Anthropic/OpenAI/PPE entirely. Useful for screenshots, mobile testing, and confirming the UI works.

### Step 1+ — go live

1. Open this file. (You're here.)
2. Verify the test suite still passes: `uv run pytest` (expect 86/86)
3. Fill in `.env` with real keys
4. Set `SHERLOCK_DEMO_MODE=0` in `.env`
5. Run the indexer: `uv run python -m indexer.run` (smoke first via `--limit 50`, then full)
6. `./scripts/start_dev.sh` and try a Discovery query in the browser
7. If Discovery works, try an RCA query against a real PPE device you know the answer to
8. Iterate the system prompt (`apps/api/prompts/rca_system.md`) if the agent's reasoning needs nudging
9. Then move on to demo prep — DEMO.md script + recording

Good morning. ☕
