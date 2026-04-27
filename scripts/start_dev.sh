#!/usr/bin/env bash
# One-command dev runner. Brings up Postgres, the FastAPI backend, and the
# Vite dev server in the foreground. Stop everything with Ctrl-C.
set -euo pipefail

cd "$(dirname "$0")/.."

cleanup() {
  echo ""
  echo "→ stopping all services…"
  jobs -p | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "→ ensuring Postgres + pgvector is up"
docker compose up -d postgres
docker compose ps

echo ""
echo "→ deploying pgvector schema (idempotent)"
uv run python -m indexer.db || {
  echo "schema deploy failed; check that Postgres is healthy"
  exit 1
}

echo ""
echo "→ starting FastAPI backend on :8000"
uv run uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!

echo ""
echo "→ starting Vite dev server on :5173"
( cd apps/web && npm run dev ) &
WEB_PID=$!

echo ""
echo "============================================================"
echo "  Sherlock is running!"
echo ""
echo "  Web UI:     http://localhost:5173"
echo "  API:        http://localhost:8000/health"
echo ""
echo "  Press Ctrl-C to stop everything."
echo "============================================================"

wait -n $API_PID $WEB_PID
