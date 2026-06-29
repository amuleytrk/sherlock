# Sherlock — Handoff Brief

> For the next AI agent picking up Sherlock work. You have **zero prior context**. This doc gets you productive in ~10 minutes of reading.
>
> The user is a senior backend engineer at Trackonomy (IoT platform, multi-tenant). They want to **refine output quality** of the existing Sherlock app, **enhance** current functionality, and **fix outdated things** the previous agent flagged. They prefer concise, opinionated answers; no overengineering; no marketing fluff; security as highest priority. See "User preferences" below.

---

## 1. What Sherlock is (60 seconds)

Sherlock is an **internal web app** that answers two recurring questions for Trackonomy engineers/operations/CS:

1. **"Why did this break?"** Multi-service Root Cause Analysis across Trackonomy's IoT platform. Today that takes 2–4 hours of senior-engineer time stitching logs across `external-service`, `ingress-service`, `event-preprocessor`, `device-management`, `location-preprocessor`, etc. Sherlock does it in seconds.
2. **"Does an API exist for X?"** Grounded RAG with file:line citations. Today that takes a 15–30 min Slack ping to a platform engineer.

Plus a third capability: **proactive briefings** — scheduled health probes that produce a markdown brief of anomalies before anyone asks. (Proactive mode is currently disabled: `SHERLOCK_PROACTIVE_ENABLED=0` — see §8 runtime reality.)

All read-only by design. Secrets never cross the LLM trust boundary. Runs locally today (FastAPI + React on the operator's machine); deployment to internal Azure is documented in `DEPLOYMENT.md`.

**Stack:** Python 3.13 / FastAPI / SSE · React + Vite + Tailwind · Postgres 16 + pgvector + tsvector hybrid · Claude Haiku 4.5 (router/verifier) → Sonnet 4.6 (worker) → Opus 4.7 (synthesis escalation) · OpenAI `text-embedding-3-large` (3072d) · five read-only MCP servers · SQLite for sessions/audit. Two AI vendors only (Anthropic + OpenAI).

**Platform:** Azure **PostgreSQL** (data-store 2.0, schema `trk`), subscription `trk-mt-ppe-sub`, server `trk-mt-ppe-pgsql-eus2`, db `dbtrkmtppe`. Reached over the operator's VPN. (Formerly MSSQL — fully cut over in `feat/pg-ppe-cutover`, merged to `main`.)

**Authorization layer:** A `/v3` JWT/Auth0 n-level authorization layer is indexed for Discovery (API questions about authz routing, permission catalogs, and route access). Deep authz RCA tooling is deferred to a later phase.

---

## 2. Required reading (priority order)

| # | Path | Purpose | When to read |
|---|---|---|---|
| 1 | `README.md` | First-impression repo doc — capabilities, architecture, setup, run, multi-env, demo mode | First (5 min) |
| 2 | This file (`HANDOFF.md`) | What you're reading now | Already done |
| 3 | `DEPLOYMENT.md` | Azure-native deployment plan reusing existing infra (~$10/mo new spend) | Only if deploying — skip otherwise |
| 4 | `apps/api/prompts/rca_system.md` | RCA agent system prompt | Before any RCA quality work |
| 5 | `apps/api/prompts/discovery_system.md` | Discovery agent system prompt | Before any Discovery quality work |
| 6 | `apps/api/prompts/router_system.md` | Haiku-4.5 intent classifier prompt | Before touching routing |
| 7 | `~/plans/work/sherlock-pg-repoint/spec.md` | PG cutover spec — WHY, binding decisions, scope | If making architectural changes |
| 8 | `~/plans/work/sherlock-pg-repoint/01-ground-truth-pg-ppe.md` | Live DB ground truth (schema, tables, ENUMs, partition layout, Cosmos containers, Redis patterns) | Before writing any DB queries |
| 9 | `~/plans/work/designs/rca-tool/2026-04-25-brainstorm-log.md` | Decision history — *why* Sherlock is shaped the way it is. **Must-read** if making architectural changes | If proposing architectural changes |
| 10 | `git log --oneline -30` | Recent commit history | When you need to know "when did X land" |

The `~/plans/work/` Obsidian vault is the user's design notebook and **lives outside the repo**. It's the canonical place for plans, decision logs, and ground-truth captures.

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
| `verify.py` | Trust layer — regex-extract claims (HTTP endpoints, `trk.*` tables, feature flags) → Haiku grader → per-claim score + aggregate band |
| `env_context.py` | `active_env` ContextVar + `EnvCreds` dataclass (PostgreSQL only; `active_system` toggle removed) |
| `settings.py` | Pydantic Settings + `env_config(env)` dynamic per-env credential lookup (PG + Cosmos + Redis + kubectl) |
| `store.py` | SQLite — sessions, messages, audit_log, briefings, claim_evals. Cascade delete with `rca_<hex8>` path-traversal guard |
| `audit.py` | `TimedTool` ctx mgr + secret-redaction regex (env-var assignments, JWT, Bearer, AKIA, sk-, URL passwords) |
| `sse.py` | Server-Sent Events helpers. Event names listed in module docstring |
| `demo.py` | Demo mode — canned realistic agent traces keyed by query patterns (no API calls / no creds needed). Updated to PG + /v3 scenarios. |
| `prompts/*.md` | Agent system prompts, grounded to PG platform |

### `mcp_servers/` — five read-only tool servers

| Server | Purpose |
|---|---|
| `sherlock_rag/server.py` | Hybrid pgvector + tsvector search via Reciprocal Rank Fusion. Corpus is PG-only (10,269 chunks, all tagged `postgres`, from `release_2.1` repos + PG/n-level design docs) |
| `trk_postgres/server.py` | Parameterized SELECT templates only (`mcp_servers/trk_postgres/templates.py` catalog). 12 query types. Read-only enforced: `default_transaction_read_only=on`. Deterministic-UUID derivation via MD5: `account_id=md5(customer_id#authorized_group)`. Schema `trk`. (Replaces the former `trk_mssql`.) |
| `trk_cosmos/server.py` | Point reads + SELECT-only Cosmos SQL. DML keywords rejected at tool layer |
| `trk_redis/server.py` | GET/HGETALL/EXISTS/ZSCORE via 5 named key patterns (iDict, pids_to_limes, etc.) |
| `trk_kubectl/server.py` | Read verbs only. Per-subprocess KUBECONFIG injection — never mutates user's `az login` |
| `trk_datadog/server.py` | Auto-hidden when DATADOG keys absent. Used as fallback to kubectl for older logs |

**Removed:** `trk_mssql` is deleted. The `active_system` mssql/postgres context variable and the frontend `SystemSwitcher` toggle are removed. PostgreSQL is the only system.

### `indexer/` — corpus pipeline

| File | Purpose |
|---|---|
| `run.py` | CLI. Crawls `repos/` (worktree-locked on `release_2.1`) + PG/n-level design docs. All chunks tagged `postgres`. MSSQL-era docs excluded. |
| `crawl.py` | File classifier (api_route, controller, architecture, planning_doc, etc.) + walker with `EXCLUDE_PATH_PREFIXES` |
| `parse.py` | Markdown heading-tree parser preserving parent/child link |
| `parse_code.py` | tree-sitter JS/TS chunker — one chunk per method |
| `chunk.py` | Chunk records with deterministic SHA-256 IDs + tokenization budget |
| `embed.py` | OpenAI batch embed (3072d) + pgvector upsert with `halfvec(3072)` HNSW cast |
| `db.py` | Schema deploy. `vector_store.chunks` table + halfvec HNSW + tsvector GIN |
| `secret_scan.py` | `detect-secrets` gate — chunks containing live secrets are dropped before embedding |
| `branches.py` | Loads `repos.yml` — which release branch each repo should be on (currently `release_2.1`) |

### `scripts/`

- `start_dev.sh` — one-command launcher (Postgres + uvicorn + Vite). **macOS bash 3.2 compat — no `wait -n`, no unicode in echo strings.** Kills orphans on :8000/:5173 before starting. Gates Vite on `/health` 30s timeout.
- `start_tunnel.sh` — cloudflared free-tier wrapper for mobile demos
- `preflight.py` — per-env tool reachability checks
- `prepare_repos.py` — git worktree setup against branches declared in `repos.yml`
- `inspect-corpus.sql` — DBeaver-friendly queries against `vector_store.chunks`
- `smoke.sh` — quick end-to-end check

### `tests/`

181 tests passing. Unit + live (skipped without creds) + regression (`tests/regression/`). Run `uv run pytest`.

---

## 4. The three modes (Chat / Briefings / Trace)

Frontend mode tabs above the chat surface (`apps/web/src/App.jsx`). Same backend, different surfaces.

### Chat — Discovery + RCA

- **Discovery** (`agents/discovery.py`): hybrid pgvector + tsvector retrieval → Sonnet 4.6 → SSE stream → trust-layer verification (`verify.py`). Corpus is PG-only.
  - After answer streams, a separate Haiku call grades each factual claim against cited chunks. Result rendered as a green/yellow/red confidence badge in the UI.
- **RCA** (`agents/rca.py`): filesystem-as-context loop. Investigates via MCP tools (PG, Cosmos, Redis, kubectl), builds an evidence dir at `investigations/<rca_id>/`, writes `final-rca.md` via the `write_final_rca` tool. Opus 4.7 escalates synthesis if cap hit.
  - User message starts with `<env>` block (name, k8s_namespace, k8s_pod_suffix).
  - **DB-state-first:** No live service logs currently (operator lacks AKS Cluster User Role; Datadog not configured). RCA runs on PG + Cosmos + Redis state and says so explicitly in its output. The AAD-kubeconfig kubectl path is staged in `.env` comments; enable when the AKS role lands.
  - On `write_final_rca` with empty markdown payload (real Opus failure mode), retries once with a directive prompt + recent evidence summary, then falls back to a synthesis stub.

### Briefings — proactive

- `proactive/scheduler.py` runs in the FastAPI lifespan. Cron tick every `SHERLOCK_BRIEFING_INTERVAL_SECONDS` (default 6h), plus one on startup if `SHERLOCK_BRIEFING_ON_STARTUP=1`.
- Four probes run in parallel against active env's kubectl; each anomaly gets a Haiku "likely cause + next step".
- Output rendered as markdown brief in the Briefings tab. **Persists across `SHERLOCK_EPHEMERAL_SESSIONS=1` startup wipes** (own lifecycle).
- **Currently disabled** (`SHERLOCK_PROACTIVE_ENABLED=0`) — probes depend on live kubectl logs, which require the AKS role. Enable when the role is granted.

### Trace — cross-service request

- User pastes qrcode / tape_id / correlation_id (UUID).
- Pipeline (`trace/pipeline.py`) detects identifier kind and returns candidate services.
- `asyncio.gather` fans out kubectl log reads (~3–5s for 5 services).
- Stitcher walks each service's logs, two-pass match on identifier + propagated correlation_ids (incl. Event Grid IDs).
- Mermaid `sequenceDiagram` rendered client-side, error events highlighted in red.
- Haiku narrative summary appended.
- **Note:** Trace requires kubectl access. Currently returns no log data until the AKS Cluster User Role is granted.

---

## 5. Recent shipped features (commit-by-commit)

(For full detail, run `git log --oneline -30` on the repo.)

**PG cutover (`feat/pg-ppe-cutover` → `main`, 2026-06-28–29):**

- **`f39c20c`** — Final MSSQL cleanup: preflight, live test, pymssql dep removed, trace uuid test fixed.
- **`cdaaca6`** — PG demo scenarios + /v3 authz demo; frontend SystemSwitcher removed (system toggle gone).
- **`72a21fd`** — Re-grounded `verify.py`, trace pipeline/stitcher, proactive probes to PG + location-preprocessor `device_event`.
- **`974d15a`** — Re-grounded RCA/Discovery/router prompts to PG; fixed `trk_cosmos`/`trk_redis` catalogs.
- **`82b6bcf`** — Wired `trk_postgres` into agents; removed `active_system` toggle, `trk_mssql`, all MSSQL backend traces.
- **`87381e8`** — `trk_postgres` MCP server: read-only PG + 12 templates + pg_* env config.
- **`2eb7ff9`** — Corpus repointed to `release_2.1`, PG-only tagging, MSSQL docs excluded.

**Pre-cutover baseline (still valid):**

- **`6ed5e19`** — Proactive Briefings + Cross-Service Trace + Trust Layer. Mode tabs Chat/Briefings/Trace added to UI.
- **`f508ca7`** — Persistent SQLite chat sessions, multi-env stage/ppe, per-tool availability dots.
- **`6cba2a6`** — Session cascade-delete + opt-in `SHERLOCK_EPHEMERAL_SESSIONS` flush-on-startup.
- **`097194f`** — `write_final_rca` hardening: empty/missing `markdown` no longer crashes Opus escalation.
- **`26f50e3`** — `DEPLOYMENT.md`.

---

## 6. What changed in the PG cutover + current known limitations

### What changed (cutover complete)

The following are resolved. Do not treat these as open items.

- **`trk_mssql` deleted; `trk_postgres` is the only DB tool.** 12 query types, read-only, schema `trk`, deterministic-UUID derivation: `account_id = md5(customer_id#authorized_group)` (raw MD5, hyphenated, no version/variant bits).
- **`active_system` / `SystemSwitcher` removed.** PostgreSQL is the sole system; no runtime toggle.
- **Corpus re-indexed PG-only.** 10,269 chunks, all tagged `postgres`, sourced from `release_2.1` repos + PG/n-level design docs. MSSQL-era docs excluded.
- **Milestone pipeline corrected.** `device_event` INSERT is owned by **location-preprocessor** (not ingress-service). `raw_device_event` by event-preprocessor/ingress. CargoIQ `device_event` by device-management. Brinks milestone by ingress.
- **`/v3` layer indexed.** JWT/Auth0 n-level authorization playbook + permission catalog + route map are indexed so Discovery can answer `/v3` questions with citations.
- **System prompts re-grounded.** RCA/Discovery/router prompts reference PG tables, `trk` schema, and the corrected pipeline. No MSSQL identity claims remain.
- **All tests pass:** `uv run pytest` = 181 passed.
- **App verified:** `/health` 200, `/envs` shows postgres✓ cosmos✓ redis✓ kubectl✗ datadog✗.
- **Live PG query confirmed:** `trk_postgres` queried real PG PPE data; a write attempt is blocked (`ReadOnlySqlTransaction`).
- **Live RCA confirmed:** a real device produced a correct PG-grounded `final-rca.md` (DB-state-first, noted logs unavailable).
- **Discovery confirmed:** PG questions answered with file:line citations; zero MSSQL hallucinations observed.

### Known limitations (active follow-ups)

1. **Live logs pending AKS role.** The operator lacks the AKS "Cluster User Role." `kubectl` returns no output; `/envs` shows `kubectl: false`. RCA runs DB-state-first and says so. Proactive briefings are disabled (`SHERLOCK_PROACTIVE_ENABLED=0`). The AAD-kubeconfig path is staged in `.env` comments — enable once the role is granted.
2. **PG user is read-write.** The supplied user `trk_mt_readwrite` has write permissions. Read-only is enforced in code (`default_transaction_read_only=on`) and tested (write attempt blocked). Recommend provisioning a dedicated read-only role as a proper follow-up.
3. **`device_event` templates default to current year.** Templates in `trk_postgres` default the `year` partition to the current year. Pass an explicit `year` param for older events. The RCA agent already does this; the MCP tool description documents it.
4. **`raw_events_check`/`device_events_recent` use `since_ts` param.** The RCA agent occasionally guesses `doy_*` param names on first try, then self-corrects. A description tweak to the tool templates could eliminate this.
5. **Minor corpus residue.** `customer-docs/microsoftMQTT.md` references old MSSQL table names (8 chunks). Exclude from indexer if it causes Discovery confusion.
6. **Deep `/v3` authz RCA deferred.** The `AUTHZ_DEBUG` intent, scope-violation→layer RCA tooling, and Auth0 lookups are a later phase. Discovery can answer `/v3` API questions from the corpus; it cannot yet do structured authz RCAs.

---

## 7. Trackonomy domain crash course

You need this to understand what users ask Sherlock and what RCAs look at.

| Term | What it is |
|---|---|
| **tape** | A Trackonomy IoT device — sticks to a parcel or asset, reports location/sensor data |
| **tape_id** | 12-hex-char MAC address of a tape. Sherlock normalizes to uppercase |
| **qrcode** | Outer label printed on the tape. Format like `9E-070524-N29401` |
| **asset_barcode** | Customer's own ID for the thing being tracked (e.g. their shipping label) |
| **customer_id** / **authorized_group** | Multi-tenant identifiers. `account_id` is derived: `md5(customer_id#authorized_group)` |
| **application_id** | Device type/config (`delta-cargo-acceptance`, `premium-high-priority`, etc.). Scoped under account. |
| **feature flags** | Stored in `trk.configuration WHERE type='FEATURE'` as JSONB `data`. Per-application behavior flags (`enableEventCollation`, `collation.MESH_VIA_LIME`, etc.) |
| **labelling** | Registering a device — POST `/devices/v1/parcel` (or `/gateway`, `/plug`, `/milestone`) on `device-management-service` |
| **milestone** | Customer-pushed shipment checkpoint (On Truck, OFF TRUCK, Zone IN VAULT). Sent via POST `/external/messages` with `message_type: MILESTONE` |
| **device_event** | PG table (formerly `lookup_parcels`). Partitioned LIST by `year`, then 4 quarter children. Holds milestone history + location events. INSERT owned by **location-preprocessor** |
| **raw_device_event** | PG table (formerly `proximity_db`). 366 DOY partitions. Raw telemetry. INSERT owned by event-preprocessor/ingress |
| **proxencoded** | BLE/cellular telemetry event from a tape. GET `/ingress/v1/proxencoded?id=<tape_id>&G1=…&scantype=…` (mobile) or POST `/post-proxencoded` (Event Grid webhook) |
| **scantype** | `type_scan_type` ENUM: IN_MESH, IN_MESH_MILESTONE, CARGO_IQ, END_OF_JOURNEY, ACTIVATION, GPS, CELLULAR, HEARTBEAT_WALLPLUG, HEARTBEAT_GATEWAY, HEARTBEAT_TEST |
| **Event Grid** | Azure pub/sub. Services hop messages through topics like `trk-mt-v2-{env}-eg-preprocessor`. Each EG message carries an `id` UUID — these are the keys that stitch a request across services |
| **correlation_id** | Per-request UUID propagated in JSON logs. The cross-service trace pipeline uses both correlation_ids and EG IDs to assemble timelines |
| **/v3 auth layer** | JWT/Auth0 n-level authorization. Permissions modeled as `(action, resource)` pairs. Routes map to required permission sets. Indexed in corpus; deep RCA tooling deferred. |

### Milestone pipeline (the canonical multi-service flow, PG era)

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
            ├─ upsert Cosmos milestones doc
            └─ publishes to location-preprocessor
                 │
                 ▼
            location-preprocessor
                 └─ INSERT trk.device_event    ◄── partitioned LIST by year
```

Failures cluster at the `device_event` INSERT (location-preprocessor) and at Event Grid hops. The old `ingress-service → INSERT lookup_parcels` pattern is gone.

### Environments

- **PG PPE** — `trk-mt-ppe-sub` subscription. Server `trk-mt-ppe-pgsql-eus2`. DB `dbtrkmtppe`. Schema `trk`. Reached over VPN. **This is the active target.**
- **AKS (PPE)** — same subscription. kubectl access pending AKS Cluster User Role grant.
- *(Stage and Prod PG environments exist but are not yet wired into Sherlock. Adding them requires only `.env` changes — see §8.)*

---

## 8. Operational knowledge

### Running locally

```bash
cd /Users/aadityamuley/Documents/repository/sherlock
./scripts/start_dev.sh        # starts Postgres + uvicorn :8000 + Vite :5173
# open http://localhost:5173
```

VPN must be up to reach PG PPE, Cosmos, and Redis. Startup runs the proactive scheduler if `SHERLOCK_PROACTIVE_ENABLED=1` — leave at `0` until kubectl access is available.

### Running tests

```bash
uv run pytest                 # full unit suite (~15s, 181 tests)
uv run pytest tests/test_indexer_*.py    # indexer-only
uv run pytest -m regression   # known-answer end-to-end — requires PPE creds
```

### Inspecting state

- Sessions/messages/audit: `sqlite3 sherlock.db ".tables"` then SELECT against `sessions`, `messages`, `audit_log`, `briefings`, `claim_evals`.
- Corpus: `docker exec sherlock-postgres psql -U sherlock -d sherlock -c "SELECT system, count(*) FROM vector_store.chunks GROUP BY system;"`
  - Expected: `postgres | 10269`
- Recent RCA evidence: `ls -lt investigations/`

### Live demo identifiers (real PPE data)

Useful when you need a real reproducer for an RCA or trace:

- **Active PPE tape** — find a live device_id via `trk_postgres` `device_recent_health` or `device_by_qrcode` templates against real PG PPE data.
- **Feature flag check** — `trk_postgres` `feature_flags` template: pass `application_id` and inspect the JSONB `data` column from `trk.configuration WHERE type='FEATURE'`.

### Environment flags

| Flag | Default | Purpose |
|---|---|---|
| `SHERLOCK_ENVS` | `ppe` | Comma-separated env names the UI dropdown offers |
| `SHERLOCK_DEFAULT_ENV` | `ppe` | Env the dropdown defaults to |
| `SHERLOCK_DEMO_MODE` | `0` | Canned PG + /v3 scenarios (no creds needed) |
| `SHERLOCK_EPHEMERAL_SESSIONS` | `0` | Wipe sessions + scratch dirs at startup (briefings preserved) |
| `SHERLOCK_PROACTIVE_ENABLED` | `0` | Run briefing scheduler (requires kubectl; keep off until AKS role is granted) |
| `SHERLOCK_BRIEFING_ON_STARTUP` | `1` | Fire one briefing immediately on boot |
| `SHERLOCK_BRIEFING_INTERVAL_SECONDS` | `21600` | Cron tick interval |
| `KUBECONFIG_<ENV>` | (none) | Self-contained admin kubeconfig per env. Subprocess-injected; never mutates user's `az login` |

---

## 9. User preferences (calibrated over many sessions)

- **Concise communication.** No unnecessary preamble. State results directly. End-of-turn summary one or two sentences max.
- **Opinionated technical decisions.** Don't list 5 options — pick the best and justify briefly. Plans are decision records, not option menus.
- **Security highest priority.** Read-only is non-negotiable. Don't propose any tool that mutates state. Don't ever cross the LLM trust boundary with credentials.
- **No far-fetched assumptions.** Especially no fabricated dollar figures, adoption percentages, or savings projections without grounding.
- **No marketing fluff.** No "leveraging cutting-edge AI", no "revolutionizing", no rubric-scoring breakdowns. Factual, technical writing only.
- **Ship-oriented.** Pragmatic over perfect. If something works for the demo, don't refactor it unless asked.
- **Minimal unsolicited refactors.** Don't introduce abstractions beyond what the task requires. Don't add error handling for impossible cases.
- **Don't create new MD/README files unless asked.** Work from conversation context, not intermediate files. The user creates the planning docs in `~/plans/work/` themselves.
- **All planning artifacts go to `~/plans/work/<project>/`**, never inside the repo, unless explicitly requested.
- **Plans use WHY > WHAT > HOW.** Start with the problem, then the solution shape, then the steps.
- **For UI work, actually test in a browser.** Type checks pass ≠ feature works.
- **Frequent, small commits.** Use `/github-commit` workflow; conventional commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).
- **The user runs servers themselves.** When you start `start_dev.sh` for a smoke test, stop it before handing back.

---

## 10. What NOT to do

- **Don't break the read-only invariant** of any MCP tool. Never expose write operations to the agent.
- **Don't commit `.env`** or bearer tokens.
- **Don't add new AI providers.** Stack is locked to Anthropic + OpenAI only.
- **Don't introduce LangChain / LangGraph / OpenClaw.** Explicitly rejected in the brainstorm log.
- **Don't reintroduce Datadog as primary log source.** It's being decommissioned. kubectl is primary; Datadog auto-hides when keys absent.
- **Don't add a new server / new k8s cluster / new database** without confirming with the user.
- **Don't write hackathon-framed copy** anywhere in the repo.
- **Don't rewrite the system prompts wholesale.** Make surgical fixes — keep the methodology + tone.
- **Don't break `start_dev.sh` for macOS bash 3.2.** No `wait -n`, no unicode in echo strings.
- **Don't break the prompt cache.** The Discovery system prompt is wrapped in `cache_control: ephemeral`. Per-env content goes in the user message, NOT the system prompt.
- **Don't surface raw secrets in audit log.** `apps/api/audit.py` redact regex uses lookbehind `(?<![A-Za-z0-9])` (NOT `\b`) — needed because env-var assignments like `DATADOG_API_KEY=` have an `_` before the keyword. Don't "simplify" the regex.
- **Don't re-introduce MSSQL, `trk_mssql`, `active_system`, or `SystemSwitcher`.** The cutover is permanent.

---

## 11. Suggested first task

If the user gives no specific direction, focus on the known limitations in §6 — highest value in order:

1. **AKS role** — once granted, flip `KUBECONFIG_PPE` in `.env` and re-enable `SHERLOCK_PROACTIVE_ENABLED=1`. Verify `/envs` shows `kubectl: true`. Smoke a Trace query.
2. **`device_events_recent` / `raw_events_check` param description** — a one-line description tweak in `trk_postgres/templates.py` to clarify `since_ts` vs `doy_*`. Low effort, eliminates a self-correction loop in RCA.
3. **Dedicated read-only PG role** — coordinate with infra to provision `trk_mt_readonly`; update `PG_PPE_*` creds in `.env`. No code change required.

**To verify any change you make:**

1. `uv run pytest` — must stay green.
2. `./scripts/start_dev.sh` — server boots cleanly, `/health` returns 200, `/envs` shows postgres✓.
3. Smoke a Discovery query: `curl -sN -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"message":"how does feature flag enableEventCollation work?","env":"ppe"}' | head -30` — confirm SSE events stream with PG citations.
4. Stop the server when done (`pkill -f "uvicorn apps.api.main"; pkill -f vite`).

---

## 12. Repo + git state

- **GitHub**: https://github.com/amuleytrk/sherlock (public)
- **Branch**: `main` (`feat/pg-ppe-cutover` merged)
- **License**: Apache 2.0 (`LICENSE`)
- **Latest commit at handoff**: `ffa92d8 docs: add HANDOFF.md + apply doc-audit fixes` (pre-cutover) + the 7 PG cutover commits above.

Good hunting.
