#!/usr/bin/env bash
# Build + deploy to Firebase: static Hosting site AND the Cloud Function proxy.
#
# Prerequisites (one-time):
#   1. firebase login                            (use sailyn@gmail.com)
#   2. Upgrade project polygence-pubmed-graphrag to the Blaze plan
#      (Cloud Functions require Blaze; free tier covers a demo).
#      https://console.firebase.google.com/project/polygence-pubmed-graphrag/usage/details
#   3. scripts/set-gemini-key.sh                 (stores GEMINI_KEY secret)
#
# Ships to: https://polygence-pubmed-graphrag.web.app

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v firebase >/dev/null 2>&1; then
  echo "✗ firebase CLI not found. Install with: npm i -g firebase-tools" >&2
  exit 1
fi

if [ ! -d "$ROOT/functions/node_modules" ] || [ ! -d "$ROOT/web/node_modules" ]; then
  echo "node_modules missing — running scripts/install.sh first…"
  "$ROOT/scripts/install.sh"
fi

cd "$ROOT/web"
echo "→ npm run deploy:firebase"
echo "  (builds static export, deploys hosting + functions in one step)"
exec npm run deploy:firebase
