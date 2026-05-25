#!/usr/bin/env bash
# Start the local Next.js dev server.
#
# Uses the LLM provider defined in web/.env.local (LLM_PROVIDER=claude-cli
# by default — spawns `claude -p` headlessly with your Claude Code auth).
# No Firebase pieces are involved in local dev.
#
# App opens at http://localhost:3000.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT/web/node_modules" ]; then
  echo "web/node_modules not found — running scripts/install.sh first…"
  "$ROOT/scripts/install.sh"
fi

if [ ! -f "$ROOT/data/graph.json" ]; then
  echo "data/graph.json not found — building reasoning graph from TSVs…"
  ( cd "$ROOT" && python3 preprocess/build_graph.py )
fi

cd "$ROOT/web"
echo "→ next dev   (http://localhost:3000)"
exec npm run dev
