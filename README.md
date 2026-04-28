# Sherlock

> AI-powered RCA + API discovery for the Trackonomy IoT platform.
> Built for the **Trackonomy Builder Challenge** (April 2026).

Sherlock is an internal web app that gives Trackonomy engineers instant answers
to two recurring asks that today bottleneck on a single platform engineer:

1. **API Discovery** — *"Does an API exist for X?"* / *"How do I use Y?"* /
   *"What does feature config flag Z control?"* / *"What can I use to achieve W?"*
   Grounded answers with file:line citations into the indexed codebase.

2. **RCA (Root Cause Analysis)** — given a vague bug report, autonomously
   investigates real PPE infrastructure (MSSQL `trk` schema, Cosmos DB, Redis,
   AKS pod logs, Datadog), drops evidence to a per-investigation scratch dir,
   and synthesizes a timeline-backed root cause writeup with Mermaid diagrams
   and matplotlib charts.

Both modes share a single chat surface and work on any device — iOS, Android,
desktop browser.

---

## Status

🚧 Under active development. Submission deadline: **April 30, 2026 EOD**.
Town Hall demo: **May 2026** (top-5 finalists, 5-min live demo).

The full implementation plan (5 days, task-by-task with code) lives in the
author's Obsidian work vault at
`~/plans/work/designs/rca-tool/implementation/`. Companion specs:
`sherlock-design.md` (architecture), `2026-04-25-brainstorm-log.md`
(decision log), `autonomous-execution-log.md` (overnight build log).

If you're picking this up cold, read [`WAKE_UP_NOTES.md`](./WAKE_UP_NOTES.md) first.

---

## Architecture

| Layer | Tech |
|-------|------|
| Frontend | React + Vite + Tailwind, dark "Technical Precision" theme |
| Backend | FastAPI + Uvicorn, SSE streaming |
| Agent runtime | Claude Agent SDK (Python), filesystem-as-context pattern |
| LLMs | Claude Haiku 4.5 (router) → Sonnet 4.6 (worker) → Opus 4.7 (escalation) |
| Embeddings | OpenAI `text-embedding-3-large` (3072d) |
| Vector store | pgvector + tsvector hybrid on local Postgres 16 |
| Tools | 6 read-only MCP servers (mssql, cosmos, redis, kubectl, datadog, rag) |
| Visualization | Anthropic Code Execution sandbox (matplotlib) + Mermaid |
| State | SQLite (`./sherlock.db`) |
| Demo tunnel | cloudflared (free tier) |

Two AI vendors total: **Anthropic** (LLM + Code Execution) and **OpenAI** (embeddings).

Full design spec lives in the author's Obsidian work vault at
`~/plans/work/designs/rca-tool/sherlock-design.md`.

---

## Security model

Credentials never cross the trust boundary into LLM prompts or LLM API calls.
The Python tool layer running on the operator's machine is the boundary.

- Secrets live only in `os.environ` (loaded from `.env`, gitignored)
- All DB users / keys / kubectl RBAC are read-only
- SQL queries use parameterized templates only — no arbitrary SQL accepted from the LLM
- Every tool output passes through a regex redaction filter for known secret patterns
- Indexing-time secret scan via `detect-secrets`
- Every tool call is audit-logged and surfaced in the UI

See `sherlock-design.md` §5 for the full controls list.

---

## Repo layout (target)

```
sherlock/
├── apps/
│   ├── api/                FastAPI + SSE + agent runners
│   └── web/                React + Vite + Tailwind
├── mcp-servers/
│   ├── trk-mssql/
│   ├── trk-cosmos/
│   ├── trk-redis/
│   ├── trk-kubectl/
│   ├── trk-datadog/
│   └── sherlock-rag/
├── indexer/                corpus indexing CLI
├── prompts/                canonical system prompts
├── tests/                  pytest; CPC-576 regression case
├── investigations/         per-RCA scratch dirs (gitignored)
├── docker-compose.yml      local Postgres for pgvector
├── pyproject.toml
└── README.md
```

---

## Setup

### Prerequisites

- macOS or Linux
- Python 3.12+ (3.13 verified) and `uv`
- Node 20+ (22 verified)
- Docker Desktop (for local Postgres + pgvector)
- `kubectl` configured against the Trackonomy PPE AKS cluster
- Read access to PPE: MSSQL (`dbtrkmtppe2`), Cosmos, Redis, Datadog
- API keys: Anthropic, OpenAI

### One-time setup

```bash
# 1. install Python deps
uv sync

# 2. install JS deps
( cd apps/web && npm install )

# 3. fill in your secrets
cp .env.example .env
$EDITOR .env

# 4. start Postgres + pgvector
docker compose up -d

# 5. deploy the pgvector schema (idempotent)
uv run python -m indexer.db

# 6. set up the 5 source repos as worktrees on their PPE release branches
#    (declared in `repos.yml`; uses your existing clones under
#    ~/Documents/repository/ when available, doesn't touch your working copy)
uv run python -m scripts.prepare_repos

# 7. build the corpus (~10 min, ~$1.20 in OpenAI embeddings)
uv run python -m indexer.run
```

### Run

```bash
# one command — Postgres + FastAPI + Vite all in the foreground:
./scripts/start_dev.sh

# open http://localhost:5173
```

For mobile / live demo, in another terminal:

```bash
./scripts/start_tunnel.sh   # cloudflared free tier, prints a public *.trycloudflare.com URL
```

### Test

```bash
uv run pytest                          # ~110 tests, ~10 sec
uv run pytest tests/test_indexer_*.py  # indexer-only
uv run pytest -m regression            # known-answer end-to-end (requires PPE creds)
```

### Updating which release branch is indexed

Each repo's PPE release branch is declared in [`repos.yml`](./repos.yml).
When releases roll over, edit `repos.yml`, then re-run:

```bash
uv run python -m scripts.prepare_repos   # update worktrees
uv run python -m indexer.run             # re-index (idempotent)
```

The indexer hard-fails if any repo on disk is on a different branch than
the one declared in `repos.yml` — preventing silent indexing of the wrong
code.

### Demo mode (no creds required)

To see Sherlock work without setting up Anthropic/OpenAI/PPE:

```bash
echo 'SHERLOCK_DEMO_MODE=1' >> .env
./scripts/start_dev.sh
# open http://localhost:5173
```

Demo mode streams canned-but-realistic agent traces for marquee queries
(see [`WAKE_UP_NOTES.md`](./WAKE_UP_NOTES.md)). Useful for the live demo
or for screenshots; turn it off (`SHERLOCK_DEMO_MODE=0`) for real
investigations.

---

## License

Internal — Trackonomy Systems only.
