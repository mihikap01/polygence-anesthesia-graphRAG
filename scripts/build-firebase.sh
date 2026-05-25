#!/usr/bin/env bash
# Build the static Next.js export for Firebase Hosting (BYOK/proxy mode).
# Does NOT deploy. Output lands in web/out/.
#
# Use this when you want to inspect the build locally before pushing, or run
# `firebase emulators:start` against it. For a one-shot deploy use
# scripts/deploy-firebase.sh instead.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT/data/graph.json" ]; then
  echo "data/graph.json not found — building reasoning graph from TSVs…"
  ( cd "$ROOT" && python3 preprocess/build_graph.py )
fi

cd "$ROOT/web"
echo "→ next build (BYOK static export)"
exec npm run build:firebase
