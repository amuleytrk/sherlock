#!/usr/bin/env bash
# Prepare the 5 source repos for indexing under $SHERLOCK_REPOS_DIR (default ./repos).
#
# For each repo we want, in priority order:
#   1. Symlink to a clone the user already has under ~/Documents/repository/
#      (avoids duplicate clones; the user's working copy is the source of truth).
#   2. Otherwise `gh repo clone` (requires `gh auth login`).
#
# Run this from the sherlock repo root.
set -euo pipefail

REPOS_DIR="${SHERLOCK_REPOS_DIR:-./repos}"
mkdir -p "$REPOS_DIR"

REPOS=(
  "Trackonomy/multi-tenant-core-services"
  "Trackonomy/multitenant-messaging-service"
  "Trackonomy/trk-mt-airline-service"
  "Trackonomy/ann-rule-engine"
  "Trackonomy/multi-tenant-dashboard"
)

LOOKUP_BASES=(
  "$HOME/Documents/repository"
  "$HOME/Documents/repository/dupe"
)

for r in "${REPOS[@]}"; do
  name="$(basename "$r")"
  dest="$REPOS_DIR/$name"

  if [[ -L "$dest" || -d "$dest" ]]; then
    echo "✓ $name already in place at $dest"
    continue
  fi

  found=""
  for base in "${LOOKUP_BASES[@]}"; do
    if [[ -d "$base/$name/.git" ]]; then
      found="$base/$name"
      break
    fi
  done

  if [[ -n "$found" ]]; then
    ln -s "$found" "$dest"
    echo "✓ symlinked $name → $found"
  else
    echo "→ cloning $r (not found locally)"
    if gh repo clone "$r" "$dest" -- --depth 1 2>/dev/null; then
      echo "  ✓ cloned"
    else
      echo "  WARN: gh clone failed for $r — will be missing from corpus"
    fi
  fi
done

echo ""
echo "Final repos in $REPOS_DIR:"
ls -la "$REPOS_DIR" 2>/dev/null | tail -n +2 || true
