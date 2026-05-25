#!/usr/bin/env bash
# Store / rotate the Gemini API key as a Firebase secret named GEMINI_KEY.
# The secret is only readable by the deployed Cloud Function — it never enters
# the JS bundle or git history.
#
# Get a key at: https://aistudio.google.com/apikey  (free tier is generous)
#
# When to run:
#   - once, before the first `scripts/deploy-firebase.sh`
#   - again whenever you rotate the key
#
# You will be prompted to paste the key. After saving, re-deploy the function:
#   scripts/deploy-firebase.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v firebase >/dev/null 2>&1; then
  echo "✗ firebase CLI not found. Install with: npm i -g firebase-tools" >&2
  exit 1
fi

echo "→ firebase functions:secrets:set GEMINI_KEY   (project: polygence-pubmed-graphrag)"
echo "  paste the Gemini key when prompted, then press Enter."
echo
firebase functions:secrets:set GEMINI_KEY

echo
echo "✓ secret saved. Re-deploy to pick it up:"
echo "    scripts/deploy-firebase.sh"
