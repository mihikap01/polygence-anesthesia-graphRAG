#!/usr/bin/env bash
# Install all dependencies: web app + Cloud Functions.
# Run once after cloning, and again after any package.json change.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ web/  (Next.js + UI)"
( cd "$ROOT/web" && npm install )

echo
echo "→ functions/  (Firebase Cloud Function)"
( cd "$ROOT/functions" && npm install )

echo
echo "✓ dependencies installed"
echo
echo "Next steps:"
echo "  scripts/dev.sh                  → run the local app (Claude CLI provider)"
echo "  scripts/set-gemini-key.sh       → store Gemini key as Firebase secret (one-time)"
echo "  scripts/deploy-firebase.sh      → build + deploy to Firebase Hosting"
