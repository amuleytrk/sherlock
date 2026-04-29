#!/usr/bin/env bash
# One-command dev runner. Brings up Postgres, the FastAPI backend, and the
# Vite dev server in the foreground. Stop everything with Ctrl-C.
#
# Hardening:
#   - Kills orphan processes on the relevant ports before starting
#     (a previous run that didn't clean up will otherwise leak Vite/uvicorn
#     processes and cause "port in use" / proxy errors)
#   - Waits for uvicorn /health to respond before launching Vite, so the
#     proxy never sees ECONNREFUSED on the first request
#   - Fails fast (and prints the uvicorn log tail) if the backend doesn't
#     come up within 30 seconds
#   - Single trap that kills both children on any exit (Ctrl-C, normal end,
#     or unexpected termination)
set -euo pipefail

cd "$(dirname "$0")/.."

API_PORT="${SHERLOCK_API_PORT:-8000}"
WEB_PORT="${SHERLOCK_WEB_PORT:-5173}"
API_LOG="/tmp/sherlock-api.log"
WEB_LOG="/tmp/sherlock-vite.log"

API_PID=""
WEB_PID=""

cleanup() {
  echo ""
  echo "→ stopping all services..."
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  jobs -p | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 1. Free up any orphaned ports from previous runs
echo "→ checking for orphan processes on :$API_PORT and :$WEB_PORT..."
for port in "$API_PORT" "$WEB_PORT" $((WEB_PORT+1)) $((WEB_PORT+2)); do
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "  killing $(echo "$pids" | wc -l | tr -d ' ') orphan process(es) on port $port: $pids"
    echo "$pids" | xargs kill 2>/dev/null || true
  fi
done
sleep 1

# 2. Postgres
echo "→ ensuring Postgres + pgvector is up"
docker compose up -d postgres
docker compose ps

# 3. Schema (idempotent)
echo ""
echo "→ deploying pgvector schema (idempotent)"
uv run python -m indexer.db || {
  echo "schema deploy failed; check that Postgres is healthy"
  exit 1
}

# 4. FastAPI backend — start, then BLOCK until /health responds
echo ""
echo "→ starting FastAPI backend on :$API_PORT (log: $API_LOG)"
: > "$API_LOG"  # truncate
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port "$API_PORT" --reload >> "$API_LOG" 2>&1 &
API_PID=$!

echo -n "  waiting for /health"
ready=0
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
    echo " — ready in ${i} half-seconds"
    ready=1
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo ""
    echo "✗ uvicorn died during startup. Last 20 lines of $API_LOG:"
    tail -20 "$API_LOG"
    exit 1
  fi
  sleep 0.5
  echo -n "."
done

if [[ "$ready" -eq 0 ]]; then
  echo ""
  echo "✗ uvicorn did not respond on :$API_PORT within 30 seconds. Tail of $API_LOG:"
  tail -30 "$API_LOG"
  exit 1
fi

# 5. Vite dev server — only AFTER backend is confirmed alive
echo ""
echo "→ starting Vite dev server on :$WEB_PORT (log: $WEB_LOG)"
: > "$WEB_LOG"
( cd apps/web && npm run dev -- --port "$WEB_PORT" --strictPort >> "$WEB_LOG" 2>&1 ) &
WEB_PID=$!

# Wait for Vite to bind (it's fast — usually <2s)
sleep 2
if ! kill -0 "$WEB_PID" 2>/dev/null; then
  echo "✗ Vite died during startup. Tail of $WEB_LOG:"
  tail -20 "$WEB_LOG"
  exit 1
fi

echo ""
echo "============================================================"
echo "  Sherlock is running!"
echo ""
echo "  Web UI:     http://localhost:$WEB_PORT"
echo "  API:        http://localhost:$API_PORT/health"
echo "  API logs:   tail -f $API_LOG"
echo "  Vite logs:  tail -f $WEB_LOG"
echo ""
echo "  Press Ctrl-C to stop everything."
echo "============================================================"

# Wait for either to exit; cleanup() (via trap) kills the other.
wait -n "$API_PID" "$WEB_PID"
