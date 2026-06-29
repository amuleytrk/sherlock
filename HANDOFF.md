# Sherlock — Handoff Brief

> For the next AI agent picking up Sherlock work. You have **zero prior context**. This doc gets you productive in ~10 minutes of reading.
>
> The user is a senior backend engineer at Trackonomy (IoT platform, multi-tenant). They want to **refine output quality** of the existing Sherlock app, **enhance** current functionality, and **fix outdated things** the previous agent flagged. They prefer concise, opinionated answers; no overengineering; no marketing fluff; security as highest priority. See "User preferences" below.

---

## 1. What Sherlock is (60 seconds)

Sherlock is an **internal web app** that answers two recurring questions for Trackonomy engineers/operations/CS:

1. **"Why did this break?"** Multi-service Root Cause Analysis across Trackonomy's IoT platform. Today that takes 2–4 hours of senior-engineer time stitching logs across `external-service`, `ingress-service`, `event-preprocessor`, `device-management`, etc. Sherlock does it in seconds.
2. **"Does an API exist for X?"** Grounded RAG with file:line citations. Today that takes a 15–30 min Slack ping to a platform engineer.

Plus a third capability: **proactive briefings** — scheduled health probes that produce a markdown brief of anomalies before anyone asks.

All read-only by design. Secrets never cross the LLM trust boundary. Runs locally today (FastAPI + React on the operator's machine); deployment to internal Azure is documented in `DEPLOYMENT.md`.

**Stack:** Python 3.13 / FastAPI / SSE · React + Vite + Tailwind · Postgres 16 + pgvector + tsvector hybrid · Claude Haiku 4.5 (router/verifier) → Sonnet 4.6 (worker) → Opus 4.7 (synthesis escalation) · OpenAI `text-embedding-3-large` (3072d) · six read-only MCP servers · SQLite for sessions/audit. Two AI vendors only (Anthropic + OpenAI).

---

## 2. Required reading (priority order)

| # | Path | Purpose | When to read |
|---|---|---|---|
| 1 | `README.md` | First-impression repo doc — capabilities, architecture, setup, run, multi-env, demo mode | First (5 min) |
| 2 | This file (`HANDOFF.md`) | What you're reading now | Already done |
| 3 | `DEPLOYMENT.md` | Azure-native deployment plan reusing existing infra (~$10/mo new spend) | Only if deploying — skip otherwise |
| 4 | `apps/api/prompts/rca_system.md` | RCA agent system prompt — **HAS DRIFT, see §6** | Before any RCA quality work |
| 5 | `apps/api/prompts/discovery_system.md` | Discovery agent system prompt — also has gaps | Before any Discovery quality work |
| 6 | `apps/api/prompts/router_system.md` | Haiku-4.5 intent classifier prompt | Before touching routing |
| 7 | `~/plans/work/designs/rca-tool/2026-04-25-brainstorm-log.md` | Decision history — *why* Sherlock is shaped the way it is. **Must-read** if making architectural changes | If proposing architectural changes |
| 8 | `~/plans/work/designs/rca-tool/implementation/day-*.md` | Original 5-day build plan. **Must-read** for grounding | If you need to know what *was* planned vs what *shipped* |
| 9 | `~/plans/work/designs/rca-tool/sherlock-design.md` | Original v1 design spec (27 KB, hackathon framing — pre-multi-env, pre-trust-layer) | Optional, deep background |
| 10 | `git log --oneline -30` | Recent commit history — the trajectory of the last ~30 commits is summarized below in §5 | When you need to know "when did X land" |

The `~/plans/work/` Obsidian vault is the user's design notebook and **lives outside the repo**. It's the canonical place for plans, decision logs, and submission artifacts.

---

## 3. Codebase map (one line per file)

### `apps/api/` — backend

| File | Purpose |
|---|---|
| `main.py` | FastAPI entry. Lifespan startup hook (ephemeral-flush + proactive scheduler). All HTTP/SSE routes: `/chat`, `/sessions[/id]`, `/rca/{id}`, `/briefings[/id]`, `/trace`, `/envs`, `/health` |
| `router.py` | Haiku-4.5 intent classifier — DEBUGGING vs API_DISCOVERY vs CONVERSATIONAL + entity extraction (tape_id, qrcode, env, etc.) |
| `agents/discovery.py` | Linear-RAG Discovery loop: hybrid search → Sonnet 4.6 streaming → trust-layer verification |
| `agents/rca.py` | RCA agent loop on raw Anthropic SDK. 18-tool-call cap, Opus 4.7 escalation, Task sub-agent dispatch, synthesis fallback |
| `agents/scratch.py` | Per-investigation `Investigation` dataclass — manages `investigations/<rca_id>/{meta.json, evidence/, analysis/, final-rca.md}` |
| `agents/code_exec.py` | Anthropic Code Execution (beta) wrapper — sandboxed Python with pre-uploaded scratch files |
| `trace/runner.py` | Cross-service trace SSE pipeline: discover → parallel kubectl → stitch → Mermaid → narrative |
| `trace/pipeline.py` | Identifier-shape detection (qrcode/tape_id/correlation_id) → candidate service list |
| `trace/log_fetcher.py` | `asyncio.gather` parallel kubectl logs (~3-5s for 5 services vs ~25s serial) |
| `trace/stitcher.py` | Timeline assembly by walking logs forward + matching propagated correlation_ids + Event Grid IDs |
| `trace/mermaid.py` | `sequenceDiagram` generator, errors highlighted via `-x` arrow |
| `proactive/probes.py` | Four shallow health probes: pod_restarts, milestone_insert_failures, redis_socket_errors, ingress_5xx |
| `proactive/briefing.py` | Orchestrator — runs probes in parallel, fires Haiku "likely cause + next step" per anomaly, writes markdown brief to SQLite |
| `proactive/scheduler.py` | Asyncio-based scheduler (no APScheduler dep), startup-fires + periodic tick |
| `verify.py` | Trust layer — regex-extract claims (HTTP endpoints, `trk.*` tables, `feature_configuration.*` flags) → Haiku grader → per-claim score + aggregate band |
| `env_context.py` | `active_env` + `active_system` ContextVars + `EnvCreds` dataclass |
| `settings.py` | Pydantic Settings + `env_config(env)` dynamic per-env credential lookup |
| `store.py` | SQLite — sessions, messages, audit_log, briefings, claim_evals. Cascade delete with `rca_<hex8>` path-traversal guard |
| `audit.py` | `TimedTool` ctx mgr + secret-redaction regex (env-var assignments, JWT, Bearer, AKIA, sk-, URL passwords) |
| `sse.py` | Server-Sent Events helpers. Event names listed in module docstring |
| `demo.py` | Demo mode — canned realistic agent traces keyed by query patterns (no API calls / no creds needed) |
| `prompts/*.md` | Agent system prompts. **Have drift — see §6** |

### `mcp_servers/` — six read-only tool servers

| Server | Purpose |
|---|---|
| `sherlock_rag/server.py` | Hybrid pgvector + tsvector search via Reciprocal Rank Fusion. Honors `active_system` filter (mssql / postgres / both) |
| `trk_mssql/server.py` | Parameterized SELECT templates only (`mcp_servers/trk_mssql/templates.py` catalog). Read-only DB user enforced upstream |
| `trk_cosmos/server.py` | Point reads + SELECT-only Cosmos SQL. DML keywords rejected at tool layer |
| `trk_redis/server.py` | GET/HGETALL/EXISTS/ZSCORE via 5 named key patterns (iDict, pids_to_limes, etc.) |
| `trk_kubectl/server.py` | Read verbs only. Per-subprocess KUBECONFIG injection — never mutates user's `az login` |
| `trk_datadog/server.py` | Auto-hidden when DATADOG keys absent. Used as fallback to kubectl for older logs |

### `indexer/` — corpus pipeline

| File | Purpose |
|---|---|
| `run.py` | CLI. Crawls `repos/` (worktree-locked release branches) + `~/plans/work/`. `_system_for(path)` tags chunks mssql/postgres/both |
| `crawl.py` | File classifier (api_route, controller, architecture, planning_doc, etc.) + walker with `EXCLUDE_PATH_PREFIXES` |
| `parse.py` | Markdown heading-tree parser preserving parent/child link |
| `parse_code.py` | tree-sitter JS/TS chunker — one chunk per method |
| `chunk.py` | Chunk records with deterministic SHA-256 IDs + tokenization budget |
| `embed.py` | OpenAI batch embed (3072d) + pgvector upsert with `halfvec(3072)` HNSW cast |
| `db.py` | Schema deploy. `vector_store.chunks` table + halfvec HNSW + tsvector GIN |
| `secret_scan.py` | `detect-secrets` gate — chunks containing live secrets are dropped before embedding |
| `branches.py` | Loads `repos.yml` — which release branch each repo should be on |

### `scripts/`

- `start_dev.sh` — one-command launcher (Postgres + uvicorn + Vite). **macOS bash 3.2 compat — no `wait -n`, no unicode in echo strings.** Kills orphans on :8000/:5173 before starting. Gates Vite on `/health` 30s timeout.
- `start_tunnel.sh` — cloudflared free-tier wrapper for mobile demos
- `preflight.py` — per-env tool reachability checks
- `prepare_repos.py` — git worktree setup against branches declared in `repos.yml`
- `inspect-corpus.sql` — DBeaver-friendly queries against `vector_store.chunks`
- `smoke.sh` — quick end-to-end check

### `tests/`

160 tests collected. Unit + live (skipped without creds) + regression (`tests/regression/`). Run `uv run pytest`.

---

## 4. The three modes (Chat / Briefings / Trace)

Frontend mode tabs above the chat surface (`apps/web/src/App.jsx`). Same backend, different surfaces.

### Chat — Discovery + RCA

- **Discovery** (`agents/discovery.py`): hybrid pgvector + tsvector retrieval → Sonnet 4.6 → SSE stream → trust-layer verification (`verify.py`).
  - User message gets a `<context>db_system: mssql|postgres</context>` preamble injected (`discovery.py:95-101`).
  - After answer streams, a separate Haiku call grades each factual claim against cited chunks. Result rendered as a green/yellow/red confidence badge in the UI.
- **RCA** (`agents/rca.py`): filesystem-as-context loop. Investigates via MCP tools, builds an evidence dir at `investigations/<rca_id>/`, writes `final-rca.md` via the `write_final_rca` tool. Opus 4.7 escalates synthesis if cap hit.
  - User message starts with `<env>` block (name, k8s_namespace, k8s_pod_suffix, db_system).
  - Env-mismatch warning surfaces if the user's text explicitly mentions a different env than the dropdown.
  - On `write_final_rca` with empty markdown payload (real Opus failure mode), retries once with a directive prompt + recent evidence summary, then falls back to a synthesis stub.

### Briefings — proactive

- `proactive/scheduler.py` runs in the FastAPI lifespan. Cron tick every `SHERLOCK_BRIEFING_INTERVAL_SECONDS` (default 6h), plus one on startup if `SHERLOCK_BRIEFING_ON_STARTUP=1`.
- Four probes run in parallel against active env's kubectl; each anomaly gets a Haiku "likely cause + next step".
- Output rendered as markdown brief in the Briefings tab. **Persists across `SHERLOCK_EPHEMERAL_SESSIONS=1` startup wipes** (own lifecycle).

### Trace — cross-service request

- User pastes qrcode / tape_id / correlation_id (UUID).
- Pipeline (`trace/pipeline.py`) detects identifier kind and returns candidate services.
- `asyncio.gather` fans out kubectl log reads (~3–5s for 5 services).
- Stitcher walks each service's logs, two-pass match on identifier + propagated correlation_ids (incl. Event Grid IDs).
- Mermaid `sequenceDiagram` rendered client-side, error events highlighted in red.
- Haiku narrative summary appended.

---

## 5. Recent shipped features (commit-by-commit)

(For full detail, run `git log --oneline -30` on the repo.)

- **`6ed5e19` "hackathon trio"** (major commit) — Proactive Briefings + Cross-Service Trace + Trust Layer. 26 new tests (131 → 157 total). Mode tabs Chat/Briefings/Trace added to UI.
- **`3b8d81a`** — MSSQL/Postgres system filter on retrieval. `chunks.system` column, `_system_for(path)` heuristic, `hybrid_search` system param, UI segmented toggle.
- **`dfc42b7`** — Verbatim path transcription rule in `discovery_system.md` after a real `/devices/v1/history` hallucination (the corpus says `/devices/v1/configs/get_history`; the LLM was paraphrasing). Trust layer now flags the wrong variant.
- **`f508ca7`** — Persistent SQLite chat sessions, multi-env stage/ppe, per-tool availability dots, monorepo sub-service retag of 1559 chunks.
- **`6cba2a6`** — Session cascade-delete (sessions, messages, audit_log, `investigations/rca_<hex8>` scratch dirs with path-traversal guard), opt-in `SHERLOCK_EPHEMERAL_SESSIONS` flush-on-startup.
- **`097194f`** — `write_final_rca` hardening: empty/missing `markdown` no longer crashes Opus escalation; one retry then synthesis stub.
- **`ca18a21`** — De-hackathon-ized README. Sherlock now positioned as a real internal Trackonomy tool.
- **`26f50e3`** — `DEPLOYMENT.md` (HEAD). Azure-native plan reusing existing AKS + PG Flex + ACR + Workload Identity + Key Vault.

Items planned in `~/plans/work/designs/rca-tool/implementation/day-5-*.md` but **NOT shipped**: `docs/DEMO.md` and `docs/SUBMISSION.md` (Day-5 Tasks 5.4/5.5). The `docs/` directory does not exist in the repo. (Hackathon submission docs live in `~/plans/work/designs/rca-tool/submission/` instead.)

---

## 6. Known-outdated things (prioritized for the new agent)

Surfaced by a recent staleness audit. These are **concrete output-quality bugs** — start here.

### Critical: `rca_system.md` advertises tools that don't exist

The RCA system prompt at `apps/api/prompts/rca_system.md` lines 65, 84–90 lists `read_file(path)`, `grep(pattern, path?)`, `find(pattern, dir?)`, `ls(dir)` under a "Filesystem tools (filesystem-as-context)" subsection. **None of these are registered in `rca.py:_tool_definitions()` or `rca.py:_MCP_DISPATCH`**. The Sonnet 4.6 agent occasionally hallucinates calls to them; the runtime rejects with "unknown tool". This is a real source of wasted tool-call budget and confused agent output.

**Two paths to fix:**
1. Implement the filesystem tools — register `fs_read` / `fs_grep` / `fs_find` / `fs_ls` in `_tool_definitions()` and `_MCP_DISPATCH` with handlers scoped to `inv.dir`. **Higher value** because it makes the filesystem-as-context narrative actually work.
2. Delete the subsection from `rca_system.md` and rewrite §3 of methodology — tell the agent that tool outputs are auto-saved to `evidence/` but the **only inspection path is `code_exec`** (the agent runs Python in the sandbox to read the files).

Same issue with `list_deployments(namespace=…)` on lines 45, 80 — implemented in `mcp_servers/trk_kubectl/server.py:108` but **not** in the RCA dispatch table. Either add it or remove the instruction.

### Critical: `rca_system.md` hardcodes MSSQL identity

Line 1 reads "MSSQL `trk` schema, Cosmos DB, Redis, Datadog" as the platform's DBs — but `active_system` can be `postgres` now (per `env_context.py` + `discovery.py` + `rca.py`). The `<env>` block carries `db_system` but the system identity at the very top of the prompt still reads MSSQL-only. Fix: "MSSQL or PostgreSQL `trk` schema (the `<env>` block tells you which)".

### Medium: `discovery_system.md` doesn't mention key context

- The user message will be prefixed by a `<context>db_system: mssql|postgres</context>` block (`discovery.py:95-101`). The prompt should explicitly tell the model to honor it.
- The answer will be auto-verified by `verify.py`. Tell the model so it produces verification-friendly output (consistent citation format).

### Medium: `router_system.md` missing `db_system` slot

`env_context.py` and `main.py` now toggle MSSQL vs Postgres, but the router's entity schema (`router_system.md:18`) has no `db_system` field. If the user message says "in the postgres system", the LLM router can't capture it. Also: the heuristic at `router.py:60` maps `dev` → `stage` but the LLM-prompt enum doesn't acknowledge that synonym.

### Medium: MCP tool descriptions hardcode PPE

`rca.py:95, 139, 152, 166` — tool descriptions still say "PPE Redis", "PPE namespace", "PPE AKS" etc. With multi-env support these contradict the `<env>` block the agent reads. Should say "the active env (resolved from the `<env>` block)".

### Low: Dead config

`apps/api/settings.py:55` declares `sherlock_log_level: str = "INFO"`. Exported in `.env.example`. **Never read anywhere.** Either wire into a `logging.basicConfig(level=s.sherlock_log_level)` call at startup, or delete both.

### Low: SHERLOCK_DEMO_MODE canned scenarios may not match current corpus

`apps/api/demo.py` has canned RCA scenarios keyed by query patterns. The Trackonomy code surface has evolved since these were written — verify each demo scenario still tells a story consistent with what live retrieval would return.

---

## 7. Trackonomy domain crash course

You need this to understand what users ask Sherlock and what RCAs look at.

| Term | What it is |
|---|---|
| **tape** | A Trackonomy IoT device — sticks to a parcel or asset, reports location/sensor data |
| **tape_id** | 12-hex-char MAC address of a tape. Sherlock normalizes to uppercase |
| **qrcode** | Outer label printed on the tape. Format like `9E-070524-N29401` |
| **asset_barcode** | Customer's own ID for the thing being tracked (e.g. their shipping label) |
| **customer_id** / **authorized_group** | UUIDs for a customer + sub-customer in `trk.customer_cfg` |
| **application_id** | Device type/config (`delta-cargo-acceptance`, `premium-high-priority`, etc.). Composite key with customer_id + authorized_group |
| **feature_configuration** | JSON column on `trk.customer_cfg`. Per-customer behavior flags (`enableEventCollation`, `collation.MESH_VIA_LIME`, `cross_customer_mesh_allowed`, `cargoIQIntergation`, etc.) |
| **labelling** | Registering a device — POST `/devices/v1/parcel` (or `/gateway`, `/plug`, `/milestone`) on `device-management-service` |
| **milestone** | Customer-pushed shipment checkpoint (On Truck, OFF TRUCK, Zone IN VAULT). Sent via POST `/external/messages` with `message_type: MILESTONE` |
| **lookup_parcels** | MSSQL table that holds the milestone history. PK = `(tape_id, facility, milestone, ts)` — colliding writes within the same epoch second fail with PRIMARY KEY violation |
| **proxencoded** | BLE/cellular telemetry event from a tape. GET `/ingress/v1/proxencoded?id=<tape_id>&G1=…&scantype=…` (mobile) or POST `/post-proxencoded` (Event Grid webhook) |
| **scantype** | 4-hex device-event type: `5258` BLE mesh, `5264` cellular/GPS, `525C` CargoIQ, `5261` BLE sensor |
| **Event Grid** | Azure pub/sub. Services hop messages through topics like `trk-mt-v2-{env}-eg-preprocessor`. Each EG message carries an `id` UUID — these are the keys that stitch a request across services |
| **correlation_id** | Per-request UUID propagated in JSON logs. The cross-service trace pipeline uses both correlation_ids and EG IDs to assemble timelines |

### Milestone pipeline (the canonical multi-service flow)

```
Customer
  └─ POST /external/messages (message_type: MILESTONE)
       │
       ▼
  external-service
       ├─ validates schema, resolves qrcode → tape_id
       ├─ reads org config (Cosmos), interprets lookup_mapping + deactivation rules
       └─ publishes to Event Grid topic eventType=milestone
            │
            ▼
       ingress-service (Generic Executor — zero milestone config knowledge)
            ├─ INSERT trk.lookup_parcels       ◄── PK: (tape_id, facility, milestone, ts)
            ├─ upsert Cosmos milestones doc
            └─ (optional) forward subject=update-device-status to device-management-service
```

This is the flow you'll see most often in RCA traces — failures cluster at `insertMilestoneLookup` and at the Event Grid hop.

### Environments

- **Stage** — `trk-mt-dev-sub` subscription. AKS cluster `aks-trk-mt-v2-shared-eastus2` (RG `rg-mt-global-v2-eastus2`). k8s namespace `stage`. Pod label suffix `-stage`.
- **PPE** — `trk-mt-prod-sub` subscription. k8s namespace `ppe`. Pod label suffix `-ppe`.
- **Postgres system (parallel migration)** — Stage on `trk-mt-nprd-sub`, PPE on `trk-mt-ppe-sub`. Mid-migration; corpus has docs from both eras.

---

## 8. Operational knowledge

### Running locally

```bash
cd /Users/aadityamuley/Documents/repository/sherlock
./scripts/start_dev.sh        # starts Postgres + uvicorn :8000 + Vite :5173
# open http://localhost:5173
```

Startup runs the proactive scheduler if `SHERLOCK_PROACTIVE_ENABLED=1` — first briefing lands in ~20–30s. Surfaces real anomalies against live PPE/Stage.

### Running tests

```bash
uv run pytest                 # full unit suite (~15s, ~160 tests)
uv run pytest tests/test_indexer_*.py    # indexer-only
uv run pytest -m regression   # known-answer end-to-end — requires PPE creds
```

### Inspecting state

- Sessions/messages/audit: `sqlite3 sherlock.db ".tables"` then SELECT against `sessions`, `messages`, `audit_log`, `briefings`, `claim_evals`.
- Corpus: `docker exec sherlock-postgres psql -U sherlock -d sherlock -c "SELECT system, count(*) FROM vector_store.chunks GROUP BY system;"`
- Recent RCA evidence: `ls -lt investigations/`

### Live demo identifiers (real PPE/Stage data)

Useful when you need a real reproducer for an RCA or trace:

- **Stage milestone PK violation** — qrcode `9E-070524-N29401` against env=stage (yesterday's duplicate-payload demo). Window 12h.
- **Active PPE tape with delta-cargo proxencoded traffic** — `F3064338067B`, customer `30b98ca8-…`, qrcode `03-190825-06-K0D546`. Trace with window=1h. May go quiet — re-find via `kubectl logs -n ppe -l app=ingress-service-ppe --tail=2000 | grep "Processing 5258"`.
- **PPE health pod with elevated restarts (briefing demo)** — `health-service-ppe-deployment-…` consistently shows 50+ restarts. The Briefings tab surfaces this every run.

### Demo prep script (off-repo)

`~/plans/work/designs/rca-tool/submission/prep.sh` (Obsidian vault, NOT in the repo — has bearer tokens). Fires the failures for the recorded demo. Do not commit. Tokens expire ~24h after issue.

### Environment flags

| Flag | Default | Purpose |
|---|---|---|
| `SHERLOCK_ENVS` | `ppe` | Comma-separated env names the UI dropdown offers |
| `SHERLOCK_DEFAULT_ENV` | `ppe` | Env the dropdown defaults to |
| `SHERLOCK_DEMO_MODE` | `0` | Canned scenarios (no creds needed) |
| `SHERLOCK_EPHEMERAL_SESSIONS` | `0` | Wipe sessions + scratch dirs at startup (briefings preserved) |
| `SHERLOCK_PROACTIVE_ENABLED` | `0` | Run briefing scheduler |
| `SHERLOCK_BRIEFING_ON_STARTUP` | `1` | Fire one briefing immediately on boot |
| `SHERLOCK_BRIEFING_INTERVAL_SECONDS` | `21600` | Cron tick interval |
| `KUBECONFIG_<ENV>` | (none) | Self-contained admin kubeconfig per env. Subprocess-injected; never mutates user's `az login` |

---

## 9. User preferences (calibrated over many sessions)

- **Concise communication.** No unnecessary preamble. State results directly. End-of-turn summary one or two sentences max.
- **Opinionated technical decisions.** Don't list 5 options — pick the best and justify briefly. Plans are decision records, not option menus.
- **Security highest priority.** Read-only is non-negotiable. Don't propose any tool that mutates state. Don't ever cross the LLM trust boundary with credentials.
- **No far-fetched assumptions.** Especially no fabricated dollar figures, adoption percentages, or savings projections without grounding. The user already pushed back on this once for the submission writeup.
- **No marketing fluff.** No "leveraging cutting-edge AI", no "revolutionizing", no rubric-scoring breakdowns. Factual, technical writing only.
- **Ship-oriented.** Pragmatic over perfect. If something works for the demo, don't refactor it unless asked.
- **Minimal unsolicited refactors.** Don't introduce abstractions beyond what the task requires. Don't add error handling for impossible cases. No premature TypeScript / no premature reranker / no premature Kubernetes.
- **Don't create new MD/README files unless asked.** Work from conversation context, not intermediate files. The user creates the planning docs in `~/plans/work/` themselves.
- **All planning artifacts go to `~/plans/work/<project>/`**, never inside the repo, unless explicitly requested.
- **Plans use WHY > WHAT > HOW.** Start with the problem, then the solution shape, then the steps.
- **For UI work, actually test in a browser.** Type checks pass ≠ feature works.
- **Frequent, small commits.** Use `/github-commit` workflow; conventional commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).
- **The user runs servers themselves.** When you start `start_dev.sh` for a smoke test, stop it before handing back.

---

## 10. What NOT to do

- **Don't break the read-only invariant** of any MCP tool. Never expose write operations to the agent.
- **Don't commit `.env`** or bearer tokens. The `prep.sh` script with stage/PPE tokens lives in `~/plans/work/designs/rca-tool/submission/` (off-repo) deliberately.
- **Don't add new AI providers.** Stack is locked to Anthropic + OpenAI only (per the brainstorm log decision).
- **Don't introduce LangChain / LangGraph / OpenClaw.** The brainstorm log explicitly rejected these.
- **Don't reintroduce Datadog as primary log source.** It's being decommissioned. kubectl is primary; Datadog auto-hides when keys absent.
- **Don't add a new server / new k8s cluster / new database** without confirming with the user. The deployment plan reuses existing infra.
- **Don't write hackathon-framed copy** anywhere in the repo. The README was de-hackathon-ized in `ca18a21`. Sherlock is positioned as a real internal tool.
- **Don't rewrite the system prompts wholesale.** The drift items in §6 are surgical fixes — keep the methodology + tone, just fix the specific incorrect parts.
- **Don't break `start_dev.sh` for macOS bash 3.2.** No `wait -n`, no unicode in echo strings — both broke it before.
- **Don't break the prompt cache.** The Discovery system prompt is wrapped in `cache_control: ephemeral`. Per-env content goes in the user message (the `<env>` / `<context>` blocks), NOT the system prompt.
- **Don't surface raw secrets in audit log.** `apps/api/audit.py` redact regex uses lookbehind `(?<![A-Za-z0-9])` (NOT `\b`) — needed because env-var assignments like `DATADOG_API_KEY=` have an `_` before the keyword. Don't "simplify" the regex.

---

## 11. Suggested first task

**If the user gives no specific direction**, start with §6 — the system-prompt drift in `rca_system.md`. That's directly an "output quality" improvement (the user's stated focus) and surgically scoped: pick either (A) implement the `fs_*` filesystem tools and `list_deployments` properly, or (B) remove the references from the prompt and update the methodology to use `code_exec` as the only scratch-dir read path. Discuss with the user which they prefer before editing.

**To verify any change you make:**

1. `uv run pytest` — must stay green.
2. `./scripts/start_dev.sh` — server boots cleanly, /health returns 200, no errors in `/tmp/sherlock-api.log` startup.
3. Smoke a Discovery query: `curl -sN -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"message":"<question>","env":"ppe","system":"mssql"}' | head -30` — confirm SSE events stream.
4. Stop the server when done (`pkill -f "uvicorn apps.api.main"; pkill -f vite`).

---

## 12. Repo + git state

- **GitHub**: https://github.com/amuleytrk/sherlock (public)
- **Branch**: `main` (no feature branches; the repo's standalone, not branched off a release_X.Y line)
- **License**: Apache 2.0 (`LICENSE`)
- **Latest commit at handoff**: `26f50e3 docs: add DEPLOYMENT.md`

Recent commits at the time of writing (newest first):

```
26f50e3 docs: add DEPLOYMENT.md — Azure-native plan, no new servers
ca18a21 docs(readme): remove hackathon-specific framing
097194f chore: submission-ready cleanup
675bdc3 docs(readme): hackathon-submission landing section
6ed5e19 feat: hackathon trio — Proactive Briefings, Cross-Service Trace, Trust Layer
3b8d81a feat: MSSQL/Postgres system filter for retrieval
dfc42b7 fix(discovery): forbid LLM rewrite of endpoint paths during synthesis
6cba2a6 feat(sessions): manual delete + opt-in startup-flush ephemeral mode
```

Good hunting.
