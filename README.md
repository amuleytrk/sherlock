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

> The setup steps below will be filled in as the implementation lands.
> For now this section documents what *will* be required.

### Prerequisites

- macOS or Linux
- Python 3.12+
- Node 20+
- Docker (for local Postgres)
- `kubectl` configured against the Trackonomy PPE AKS cluster
- Read access to PPE: MSSQL (`dbtrkmtppe2`), Cosmos, Redis, Datadog
- API keys: Anthropic, OpenAI

### Environment variables

Copy `.env.example` to `.env` and fill in the values. **Never commit `.env`.**

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
MSSQL_PPE_CONN=
COSMOS_PPE_KEY=
COSMOS_PPE_ENDPOINT=
REDIS_PPE_URL=
DATADOG_API_KEY=
DATADOG_APP_KEY=
KUBECONFIG=
DATABASE_URL=             # local Postgres for pgvector
```

### Run (planned)

```
docker compose up -d            # local Postgres
python -m indexer.run           # populate pgvector corpus (~10 min, ~$1.20)
uvicorn apps.api.main:app       # backend
cd apps/web && npm run dev      # frontend
```

---

## License

Internal — Trackonomy Systems only.
