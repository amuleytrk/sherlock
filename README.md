# Sherlock

> **Investigations that took hours, in seconds.**
> AI-powered RCA + API discovery for the Trackonomy IoT platform.
> Built for the **Trackonomy Builder Challenge** (April 2026).

`Python 3.13` · `FastAPI` · `React + Vite + Tailwind` · `Claude Haiku/Sonnet/Opus` · `OpenAI text-embedding-3-large` · `pgvector` · `157 tests passing`

---

## At a glance

Sherlock is an internal web app — usable on any device — that answers the two questions every Trackonomy engineer asks every week:

1. **"Why did this break?"** Traditional multi-service RCA: 2–4 hours of senior eng time. Sherlock: 5 seconds with a Mermaid timeline diagram.
2. **"Does an API exist for X?"** Traditional: 15–30 min Slack ping to a platform engineer. Sherlock: 30 seconds with grounded citations and a self-graded confidence badge.

Plus a third capability the brief didn't ask for but judges will love: Sherlock **runs while you sleep** and produces a morning brief of anomalies it noticed overnight.

### Three modes, one chat surface

| Mode | What it does | Lights up |
|---|---|---|
| **Discovery** | Hybrid RAG (pgvector + tsvector) + Sonnet 4.6 + Haiku self-verifier. Every answer carries a confidence badge with per-claim evidence. | Engineering / Ops / CS / Product |
| **RCA + Trace** | Filesystem-as-context agent with 12 read-only MCP tools. Cross-service trace fans out kubectl in parallel and stitches by correlation ID — full Mermaid sequenceDiagram in 5s. | Engineering / on-call |
| **Briefings** | Scheduled health probes + Haiku-authored "likely cause + next step" assessments. Survives ephemeral wipes — yesterday's brief is still there tomorrow. | Operations / Leadership |

### Why it scores on the judging rubric

- **Impact (40)** — quantified at $30–150K/year saved depending on adoption (3–15× the $10K bonus threshold). [See IMPACT.md →](#hackathon-submission)
- **Practicality (20)** — read-only by design; per-env kubeconfigs; one-command launch (`./scripts/start_dev.sh`); zero changes to existing workflows.
- **Innovation (15)** — proactive AI that works before you ask, parallel kubectl + correlation-stitching, self-grading RAG, sub-agent dispatch, monorepo-aware corpus tagging.
- **Scalability (15)** — same backend serves Engineering / Ops / CS / Leadership; multi-env + multi-DB-system selectors built in; new envs are `.env` work, no code changes.
- **Presentation (10)** — Mermaid diagrams, severity badges, color-coded confidence pills — designed for a 5-minute live demo.

### Hackathon submission

Submission artifacts live under `~/plans/work/designs/rca-tool/submission/`:

- **`SUBMISSION.md`** — the polished entry text (problem + solution + tools + impact + bonus criteria).
- **`IMPACT.md`** — deeper financial brief with sensitivity analysis, adoption modeling, and assumptions documented line-by-line.
- **`DEMO_SLIDES.md`** — 5-slide outline + speaker notes + 3-minute live-demo script for the May Town Hall.

---

## Status

🚧 Under active development. Submission deadline: **April 30, 2026 EOD**.
Town Hall demo: **May 2026** (top-5 finalists, 5-min live demo).

The full design specification + implementation plan + decision log
live in the author's Obsidian work vault at
`~/plans/work/designs/rca-tool/`. Companion specs:
`sherlock-design.md` (architecture), `submission/SUBMISSION.md` (entry
text), `submission/IMPACT.md` (financial brief), `submission/DEMO_SLIDES.md`
(5-min Town Hall script).

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
- `kubectl` on PATH
- API keys: Anthropic, OpenAI
- Per-env: read access to MSSQL / Cosmos / Redis + a self-contained kubeconfig
  (admin or SP-backed) per env you want to investigate. Sherlock currently
  ships pre-wired for **PPE** and **Stage**; flip via the dropdown in the
  top-right of the UI. Adding more envs is just `.env` work — see
  [Multi-env setup](#multi-env-setup).

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

### Top-level surfaces (Chat / Briefings / Trace)

The main pane is split across three modes you can flip via the tabs above
the chat surface:

- **Chat** — the original Discovery + RCA conversational flow. Every Discovery
  answer carries a **confidence badge** computed by a separate Haiku call
  that grades each factual claim (endpoint URL, table name, feature flag)
  against the cited corpus chunks. Badge expands to show per-claim score +
  evidence excerpt. Sub-60 confidence pins a "verify before acting" warning.

- **Briefings** — proactive mode. Sherlock runs four health probes against
  the active env (pod restarts, milestone insert failures, Redis socket
  errors, ingress 5xx) and produces a markdown brief whenever something
  looks off. A Haiku model adds a 2-3 sentence likely-cause assessment per
  anomaly. Configurable via `SHERLOCK_PROACTIVE_ENABLED` + interval; runs
  one on startup so the tab is never empty for demos. Briefings persist
  across `SHERLOCK_EPHEMERAL_SESSIONS=1` wipes (their own lifecycle).

- **Trace** — cross-service request trace. Paste any qrcode / tape_id /
  correlation_id and Sherlock fans out kubectl logs across the candidate
  services in parallel (asyncio.gather), stitches the timeline by
  identifier + propagated correlation IDs, renders the entire flow as a
  Mermaid sequence diagram with errors highlighted, and produces a Haiku
  narrative summary. End-to-end ~5s for a 3-service trace.

### MSSQL vs Postgres mode

Trackonomy is mid-migration MSSQL → PostgreSQL, and the indexed corpus has
docs from both eras. The top-right header has a small **MSSQL / Postgres**
toggle that scopes RAG retrieval to one system, so e.g. an MSSQL-mode
discovery query never surfaces PG-flavored tables like `trk.raw_device_event`.
Selection persists to localStorage.

How chunks get tagged:
- Files under `~/plans/work/designs/postgres/**`, plus filenames matching
  `postgresql*`, `postgresDeviceMgmt*`, `pgSystem*`, `dataMigrationPg.md` →
  tagged `postgres`.
- Everything else (general design docs, service code) → tagged `both`.

The filter rule: `mssql` mode returns chunks where `system IN ('mssql', 'both')`;
`postgres` mode returns `('postgres', 'both')`. So general docs and shared
service code show up in both modes — only system-era-specific docs are gated.

### Multi-env setup

Sherlock can target multiple deployment environments. Switch via the
dropdown in the top-right of the UI; each chat request carries the active
env, and every backend tool (MSSQL, Cosmos, Redis, kubectl) reads
env-specific credentials.

**The kubectl gotcha.** Stage and PPE live on different Azure
subscriptions. To avoid mutating your local `az login` context per request,
each env uses a **self-contained kubeconfig** (admin cert or service
principal) — generate once, point `KUBECONFIG_<ENV>` at it, done.

```bash
# PPE (already wired if you came from the v1 docs)
az aks get-credentials --admin \
  --subscription trk-mt-prod-sub \
  --resource-group <ppe-rg> --name <ppe-aks> \
  -f ~/.kube/sherlock-ppe.config

# Stage (subscription differs from PPE)
az aks get-credentials --admin \
  --subscription trk-mt-dev-sub \
  --resource-group rg-mt-global-v2-eastus2 \
  --name aks-trk-mt-v2-shared-eastus2 \
  -f ~/.kube/sherlock-stage.config
```

Then in `.env`:

```bash
SHERLOCK_ENVS=stage,ppe
SHERLOCK_DEFAULT_ENV=ppe

KUBECONFIG_PPE=~/.kube/sherlock-ppe.config
KUBECONFIG_STAGE=~/.kube/sherlock-stage.config

# Per-env DB/cache creds — see .env.example for the full list:
MSSQL_PPE_*    / MSSQL_STAGE_*
COSMOS_PPE_*   / COSMOS_STAGE_*
REDIS_PPE_*    / REDIS_STAGE_*

# Trackonomy convention: PPE pods are labeled `app=<svc>-ppe` in namespace
# `ppe`; stage pods are `-dev` in namespace `dev`. Defaults match these;
# override with K8S_<ENV>_NAMESPACE / K8S_<ENV>_POD_SUFFIX if needed.
```

Adding a new env (e.g. **prod**) requires zero code changes:
1. Generate a self-contained kubeconfig (admin or SP).
2. Add `MSSQL_PROD_*`, `COSMOS_PROD_*`, `REDIS_PROD_*`, `KUBECONFIG_PROD`.
3. Append `prod` to `SHERLOCK_ENVS`.
4. Restart. Dropdown picks it up.

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
  fresh. Recommended for demo builds; leave off while developing
  (restarts are common, and you'll want history to survive them).

Why startup-flush instead of shutdown-flush? Shutdown hooks don't run on
crashes / `kill -9` / OS sleep, so they're inconsistent. Startup-flush
always runs and produces the same end-user effect.

### Demo mode (no creds required)

To see Sherlock work without setting up Anthropic/OpenAI/PPE:

```bash
echo 'SHERLOCK_DEMO_MODE=1' >> .env
./scripts/start_dev.sh
# open http://localhost:5173
```

Demo mode streams canned-but-realistic agent traces for marquee queries.
Useful for screenshots; turn it off (`SHERLOCK_DEMO_MODE=0`) for real
investigations against live PPE/Stage infrastructure.

---

## License

See [`LICENSE`](./LICENSE) (Apache 2.0). Submitted to the Trackonomy Builder
Challenge — April 2026.
