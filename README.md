# Sherlock

> **Investigations that took hours, in seconds.**
> AI-powered RCA + API discovery for the Trackonomy IoT platform.

`Python 3.13` · `FastAPI` · `React + Vite + Tailwind` · `Claude Haiku/Sonnet/Opus` · `OpenAI text-embedding-3-large` · `pgvector`

---

## At a glance

Sherlock is an internal web app — usable on any device — that answers the two questions every Trackonomy engineer asks every week:

1. **"Why did this break?"** Traditional multi-service RCA: 2–4 hours of senior eng time. Sherlock: seconds, with a Mermaid timeline diagram and a written root-cause report.
2. **"Does an API exist for X?"** Traditional: 15–30 min Slack ping to a platform engineer. Sherlock: 30 seconds with grounded citations and a self-graded confidence badge.

Plus a third capability: Sherlock **runs proactively** on a schedule and produces a brief of anomalies it noticed in the platform, so engineers arrive at work with debugging already underway.

### Three modes, one chat surface

| Mode | What it does | Primary users |
|---|---|---|
| **Discovery** | Hybrid RAG (pgvector + tsvector) + Sonnet 4.6 + Haiku self-verifier. Every answer carries a confidence badge with per-claim evidence. | Engineering / Ops / CS / Product |
| **RCA + Trace** | Filesystem-as-context agent with read-only MCP tools across PostgreSQL (`trk` schema), Cosmos, Redis, kubectl. Cross-service trace fans out kubectl in parallel and stitches by correlation ID — full Mermaid sequence diagram in seconds. | Engineering / on-call |
| **Briefings** | Scheduled health probes + Haiku-authored "likely cause + next step" assessments. | Operations / Leadership |

---

## Architecture

| Layer | Tech |
|-------|------|
| Frontend | React + Vite + Tailwind, dark "Technical Precision" theme |
| Backend | FastAPI + Uvicorn, SSE streaming |
| Agent runtime | Anthropic SDK with explicit tool-use loop, filesystem-as-context pattern |
| LLMs | Claude Haiku 4.5 (router/verifier) → Sonnet 4.6 (worker) → Opus 4.7 (synthesis escalation) |
| Embeddings | OpenAI `text-embedding-3-large` (3072d) |
| Vector store | pgvector + tsvector hybrid on local Postgres 16 |
| Live data store | Azure PostgreSQL (`trk-mt-ppe-pgsql-eus2`, schema `trk`, subscription `trk-mt-ppe-sub`) |
| Tools | Five read-only MCP servers (postgres, cosmos, redis, kubectl, rag) |
| Authorization | `/v3` JWT/Auth0 n-level layer indexed in corpus for Discovery |
| Visualization | Anthropic Code Execution sandbox (matplotlib) + Mermaid |
| State | SQLite (`./sherlock.db`) |

Two AI vendors total: **Anthropic** (LLM + Code Execution) and **OpenAI** (embeddings).

---

## Security model

Credentials never cross the trust boundary into LLM prompts or LLM API calls. The Python tool layer running on the operator's machine is the boundary.

- Secrets live only in `os.environ` (loaded from `.env`, gitignored).
- All DB users / keys / kubectl RBAC are read-only.
- SQL queries use parameterized templates only — no arbitrary SQL accepted from the LLM.
- Every tool output passes through a regex redaction filter for known secret patterns.
- Indexing-time secret scan via `detect-secrets`.
- Every tool call is audit-logged and surfaced in the UI.
- Per-environment self-contained kubeconfigs avoid mutating the operator's `az login` context.

---

## Repo layout

```
sherlock/
├── apps/
│   ├── api/
│   │   ├── agents/         Discovery + RCA agent loops, code_exec, scratch dirs
│   │   ├── prompts/        canonical system prompts (Markdown), grounded to PG platform
│   │   ├── proactive/      scheduled health probes + Haiku-authored briefings
│   │   ├── trace/          cross-service request trace (pipeline → fetch → stitch → Mermaid)
│   │   ├── main.py         FastAPI entry + SSE endpoints + lifespan hooks
│   │   ├── router.py       Haiku-4.5 intent classifier
│   │   ├── verify.py       Trust layer (claim extraction + Haiku grading)
│   │   ├── env_context.py  Per-request active env contextvars
│   │   ├── settings.py     Per-env credential lookup (POSTGRES_<ENV>_*, COSMOS_<ENV>_*, etc.)
│   │   ├── store.py        SQLite — sessions, audit log, briefings, claim evals
│   │   └── audit.py        Tool-call audit + secret-redaction regex
│   └── web/                React + Vite + Tailwind (3 modes: Chat / Briefings / Trace)
├── mcp_servers/            five read-only tool servers
│   ├── trk_postgres/       12 parameterized SELECT templates, schema trk, read-only enforced
│   ├── trk_cosmos/         point reads + SELECT-only Cosmos SQL
│   ├── trk_redis/          GET / HGETALL / EXISTS / ZSCORE via key patterns
│   ├── trk_kubectl/        read verbs only; env-aware KUBECONFIG injection
│   ├── trk_datadog/        auto-hidden when credentials absent
│   └── sherlock_rag/       hybrid pgvector + tsvector retrieval
├── indexer/                corpus indexing CLI + hybrid-search schema
├── scripts/                dev runner, preflight, tunnel, prepare-repos
├── tests/                  pytest unit + live + regression suites
├── investigations/         per-RCA scratch dirs (gitignored)
├── docker-compose.yml      local Postgres for pgvector
├── DEPLOYMENT.md           Azure-native deployment plan (reuses existing infra)
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
- `kubectl` on PATH
- API keys: Anthropic, OpenAI
- VPN access to the Trackonomy Azure environment (PG PPE is on a private endpoint)
- Per-env: PostgreSQL + Cosmos + Redis credentials + a self-contained kubeconfig
  (admin or SP-backed) per env you want to investigate. Sherlock currently
  ships pre-wired for **PG PPE** (`trk-mt-ppe-sub`). Adding more envs (stage,
  prod) is just `.env` work — see [Multi-env setup](#multi-env-setup).

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

# 6. set up the source repos as worktrees on their PPE release branches
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

For mobile access from outside your network, in another terminal:

```bash
./scripts/start_tunnel.sh   # cloudflared free tier, prints a public *.trycloudflare.com URL
```

### Test

```bash
uv run pytest                          # full unit suite, ~15 sec
uv run pytest tests/test_indexer_*.py  # indexer-only
uv run pytest -m regression            # known-answer end-to-end (requires PPE creds)
```

### Updating which release branch is indexed

Each repo's release branch is declared in [`repos.yml`](./repos.yml).
When releases roll over, edit `repos.yml`, then re-run:

```bash
uv run python -m scripts.prepare_repos   # update worktrees
uv run python -m indexer.run             # re-index (idempotent)
```

The indexer hard-fails if any repo on disk is on a different branch than
the one declared in `repos.yml` — preventing silent indexing of the wrong
code.

### Top-level surfaces (Chat / Briefings / Trace)

The main pane is split across three modes you can flip via the tabs above
the chat surface:

- **Chat** — the Discovery + RCA conversational flow. Every Discovery
  answer carries a **confidence badge** computed by a separate Haiku call
  that grades each factual claim (endpoint URL, table name, feature flag)
  against the cited corpus chunks. Badge expands to show per-claim score +
  evidence excerpt. Sub-60 confidence pins a "verify before acting" warning.

- **Briefings** — proactive mode. Sherlock runs four health probes against
  the active env (pod restarts, milestone insert failures, Redis socket
  errors, ingress 5xx) and produces a markdown brief whenever something
  looks off. A Haiku model adds a 2-3 sentence likely-cause assessment per
  anomaly. Configurable via `SHERLOCK_PROACTIVE_ENABLED` + interval.
  Briefings persist across `SHERLOCK_EPHEMERAL_SESSIONS=1` wipes (their
  own lifecycle).

- **Trace** — cross-service request trace. Paste any qrcode / tape_id /
  correlation_id and Sherlock fans out kubectl logs across the candidate
  services in parallel (`asyncio.gather`), stitches the timeline by
  identifier + propagated correlation IDs, renders the entire flow as a
  Mermaid sequence diagram with errors highlighted, and produces a Haiku
  narrative summary. End-to-end ~5s for a 3-service trace.

### Corpus and platform

Sherlock is fully grounded on the **PostgreSQL** platform (data-store 2.0,
schema `trk`). The corpus (10,269 chunks) is sourced exclusively from
`release_2.1` repos and the PG/n-level design docs — all tagged `postgres`.
MSSQL-era documentation is excluded from the index.

Discovery answers questions about the PG schema, the `/v3` JWT/Auth0 n-level
authorization layer, and the `release_2.1` service codebase with file:line
citations. There is no MSSQL/Postgres toggle — PostgreSQL is the only system.

### Multi-env setup

Sherlock can target multiple deployment environments. Switch via the
dropdown in the top-right of the UI; each chat request carries the active
env, and every backend tool (PostgreSQL, Cosmos, Redis, kubectl) reads
env-specific credentials.

**The kubectl gotcha.** Stage and PPE live on different Azure
subscriptions. To avoid mutating your local `az login` context per request,
each env uses a **self-contained kubeconfig** (admin cert or service
principal) — generate once, point `KUBECONFIG_<ENV>` at it, done.

```bash
# PPE (subscription trk-mt-ppe-sub)
az aks get-credentials --admin \
  --subscription trk-mt-ppe-sub \
  --resource-group <ppe-rg> --name <ppe-aks> \
  -f ~/.kube/sherlock-ppe.config

# Stage — not yet wired (uses a different subscription/AKS cluster)
# Add when needed: --subscription trk-mt-nprd-sub
```

Then in `.env`:

```bash
SHERLOCK_ENVS=ppe
SHERLOCK_DEFAULT_ENV=ppe

KUBECONFIG_PPE=~/.kube/sherlock-ppe.config

# Per-env DB/cache creds — see .env.example for the full list:
PG_PPE_*
COSMOS_PPE_*
REDIS_PPE_*

# Trackonomy convention: PPE pods are labeled `app=<svc>-ppe` in namespace
# `ppe`. Override with K8S_<ENV>_NAMESPACE / K8S_<ENV>_POD_SUFFIX if needed.
```

Adding a new env (e.g. **stage** or **prod**) requires zero code changes:
1. Generate a self-contained kubeconfig (admin or SP).
2. Add `POSTGRES_<ENV>_*`, `COSMOS_<ENV>_*`, `REDIS_<ENV>_*`, `KUBECONFIG_<ENV>`.
3. Append the env name to `SHERLOCK_ENVS`.
4. Restart. The dropdown picks it up.

`uv run python -m scripts.preflight` runs per-env tool checks for every
configured env — use it to verify Stage credentials end-to-end before
relying on them.

### Session history & cleanup

Every chat persists to local SQLite (`./sherlock.db`) so the sidebar shows
past investigations. To clean up:

- **Trash icon** on each sidebar entry → cascade-delete that one session,
  its messages, audit rows, and the matching `investigations/<rca_id>/`
  scratch dir.
- **"Clear all"** in the sidebar header → nuke everything.
- **Auto-flush at startup** — set `SHERLOCK_EPHEMERAL_SESSIONS=1` in `.env`
  and every server boot wipes persisted state, so each launch starts
  fresh. Recommended for shared / kiosk-style instances; leave off while
  developing locally (restarts are common, and you'll want history to
  survive them).

Why startup-flush instead of shutdown-flush? Shutdown hooks don't run on
crashes / `kill -9` / OS sleep, so they're inconsistent. Startup-flush
always runs and produces the same end-user effect.

### Deployment

For shipping Sherlock as an internal Azure-hosted tool — reusing the existing
AKS cluster, ACR, Postgres Flexible Server, and Workload Identity — see
[`DEPLOYMENT.md`](./DEPLOYMENT.md). Marginal new infra spend projected at
under $10/month plus variable AI API costs.

### Demo mode (no creds required)

To explore the UI without setting up Anthropic / OpenAI / PPE access:

```bash
echo 'SHERLOCK_DEMO_MODE=1' >> .env
./scripts/start_dev.sh
# open http://localhost:5173
```

Demo mode streams canned-but-realistic agent traces for a small set of
marquee queries. Useful for screenshots, mobile testing, and showing the
UX to stakeholders. Turn it off (`SHERLOCK_DEMO_MODE=0`) for real
investigations against live infrastructure.

---

## License

See [`LICENSE`](./LICENSE) (Apache 2.0).
