# Sherlock — web

React + Vite + Tailwind frontend for Sherlock. Dark "Technical Precision" theme.

The build is launched alongside the FastAPI backend by `./scripts/start_dev.sh`
from the repo root. Vite proxies `/api/*` to `http://localhost:8000`. See the
top-level [`README.md`](../../README.md) for the full setup.

## Components

- `App.jsx` — top-level layout, mode tabs (Chat / Briefings / Trace), env + system switchers
- `components/ChatStream.jsx` — Discovery + RCA chat surface, SSE event timeline
- `components/BriefingsPane.jsx` — proactive-mode dashboard
- `components/TracePane.jsx` — cross-service trace UI with Mermaid sequence diagrams
- `components/ConfidenceBadge.jsx` — trust-layer self-graded confidence display
- `components/EnvSwitcher.jsx` / `SystemSwitcher.jsx` — env (PPE / Stage) and DB-system (MSSQL / Postgres) selectors
- `components/HistorySidebar.jsx` — session history with cascade-delete
- `lib/sse.js` / `lib/api.js` — SSE streaming + REST clients

## Local dev

```bash
npm install
npm run dev      # standalone Vite (rare — usually use scripts/start_dev.sh)
npm run build    # production bundle
```
