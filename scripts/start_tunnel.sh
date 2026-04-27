#!/usr/bin/env bash
# Expose the local Vite dev server (which proxies to FastAPI) via a public
# cloudflared tunnel. Use this for the live mobile demo.
#
# REQUIRES: cloudflared installed (`brew install cloudflared`).
# Spin up start_dev.sh in another terminal first.
set -euo pipefail

PORT="${PORT:-5173}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not installed. Install with: brew install cloudflared"
  exit 1
fi

echo "→ Starting cloudflared tunnel to http://localhost:${PORT}"
echo "  When the URL prints, keep it private — anyone with the URL can reach"
echo "  Sherlock (and your local PPE-read credentials behind it)."
echo ""
cloudflared tunnel --url "http://localhost:${PORT}"
