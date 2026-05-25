# Working in this repo (quick reference)

A pharmacogenomic GraphRAG demo. Two deploy targets share one codebase:
**local** (Next.js dev server with the Claude Code CLI) and **Firebase**
(static export + Cloud Function proxy to Gemini).

## Scripts the user expects you to know

All in `scripts/` at the repo root. They are the canonical entry points;
prefer them over invoking `npm run …` directly so output stays consistent.

| Need | Run |
| --- | --- |
| Install / re-install everything | `scripts/install.sh` |
| Start the local app | `scripts/dev.sh` (opens http://localhost:3000) |
| Just build the static export | `scripts/build-firebase.sh` |
| Store / rotate the Gemini key (Firebase secret) | `scripts/set-gemini-key.sh` |
| Build + deploy to Firebase | `scripts/deploy-firebase.sh` |

## Don't get this wrong

- **Local dev uses the Claude CLI** (subprocess). Do not propose changing
  the local provider unless the user asks.
- **The deployed Firebase site uses Gemini via a Cloud Function.** The
  Gemini key lives in a Firebase secret (`GEMINI_KEY`) and must never be
  embedded in `web/` source, committed, or printed. (DeepSeek/OpenAI/Anthropic
  are still supported as user-supplied keys via the in-app modal.)
- **Cloud Functions need the Blaze plan.** If a deploy fails with
  "billing not enabled," tell the user to upgrade the project — don't try
  to work around it.
- **`web/` and `functions/` are independent npm packages.** Install deps in
  each separately (or just run `scripts/install.sh`).
- **The `app/api/` routes are server-only.** `scripts/build-firebase.sh`
  stashes them aside during the static build and restores them after — if
  you see `app/_api_stash/` it means a build was interrupted; rename back.

## Where things live

- `web/lib/llm/index.ts` — server-side LLM facade (Claude CLI / OpenAI / none)
- `web/lib/llm/browser.ts` — browser-side: `proxyAsk()` (default) + `browserAsk()` (user key)
- `web/lib/llm/browser-pipeline.ts` — browser equivalents of `/api/explain` + `/api/chat`
- `web/lib/graph/ops.ts` — pure graph ops (neighbourhood, applyFilters)
- `web/lib/graph/loader.ts` — server-only fs reads (do NOT import client-side)
- `web/lib/graph/client-loader.ts` — browser fetches `/data/*.json`
- `web/lib/graph/data-api.ts` — facade picking API route vs. in-browser ops
- `functions/index.js` — Cloud Function proxy → Gemini (`gemini-2.0-flash`)
- `firebase.json` — hosting + functions config (rewrites `/api/llm` → `ask`)

The full README has architecture, data prep, demo walkthrough, and
trade-offs of each LLM provider. Read it before changing anything load-bearing.
