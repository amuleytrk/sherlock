#!/usr/bin/env bash
# End-to-end smoke test of the demo-mode dispatch path.
# Brings up uvicorn (no Postgres dependency in demo mode), exercises a few
# /chat queries, verifies the scratch-dir gets populated, and tears down.
#
# Run from the sherlock repo root.
set -euo pipefail

cd "$(dirname "$0")/.."

LOG=/tmp/sherlock-smoke.log
rm -rf investigations/rca_demo_*
SHERLOCK_DEMO_MODE=1 uv run uvicorn apps.api.main:app --port 8765 > "$LOG" 2>&1 &
UVI=$!
trap "kill $UVI 2>/dev/null || true" EXIT

# Wait for /health
for i in $(seq 1 20); do
  if curl -fsS http://localhost:8765/health > /dev/null 2>&1; then
    echo "→ uvicorn ready (waited ${i}s)"
    break
  fi
  sleep 0.5
done

echo ""
echo "=== /health ==="
curl -fsS http://localhost:8765/health

echo ""
echo "=== Discovery query ==="
curl -fsSN -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"How do I label a white tape device?"}' \
  | grep -E '^event:' | head -8

echo ""
echo "=== RCA query ==="
curl -fsSN -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Device AABBCCDDEEFF events not in lookup_parcels in PPE"}' \
  | grep -E '^event:' | head -20

echo ""
echo "=== Scratch dir contents ==="
LATEST=$(ls -t investigations/ 2>/dev/null | head -1 || echo "")
if [ -n "$LATEST" ]; then
  echo "rca: $LATEST"
  ls -la "investigations/$LATEST/"
  ls -la "investigations/$LATEST/evidence/"
  ls -la "investigations/$LATEST/analysis/"
  echo ""
  echo "=== final-rca.md (first 400 chars) ==="
  head -c 400 "investigations/$LATEST/final-rca.md" 2>/dev/null || echo "(final-rca.md not yet written — wait?)"
else
  echo "no investigation dir found"
fi

echo ""
echo "✓ smoke complete"
