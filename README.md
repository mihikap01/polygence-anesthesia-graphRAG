# Polygence Anesthesia GraphRAG

An interactive biomedical knowledge-graph web app for pharmacogenomic and
anesthesia-related risk reasoning. Built from the PharmGKB-style TSV
exports in this repo.

Two grounded reasoning surfaces over the graph:

- **Explain** — click any node or edge on the canvas; an LLM explains it
  using its 2-hop neighbourhood as context.
- **Chat** — type a freeform question; a real GraphRAG retrieval step
  (entity-linking → 1-hop neighbourhoods → shortest paths) is run *before*
  the LLM is called.

Every answer ships with a collapsible **"Context sent to model"** panel
showing the exact system prompt, user prompt, graph context, size, and a
copy button — so the GraphRAG step is fully observable, not magic.

```
polygence-anesthesia-graphRAG/
├── *.tsv                       # raw PharmGKB data (kept as-is)
├── preprocess/build_graph.py   # data-loading + simplification
├── data/                       # generated JSON (gitignored — rerun script)
├── web/                        # Next.js + TypeScript + Cytoscape app
├── functions/                  # Firebase Cloud Function (Gemini proxy)
├── scripts/                    # one-script-per-task wrappers (see below)
├── firebase.json, .firebaserc  # Firebase Hosting + Functions config
└── README.md
```

---

## Operations — one script per task

All wrappers live at the repo root in `scripts/` and can be run from anywhere.
They auto-install missing dependencies and build the graph data on first run.

| Script | What it does | When to run |
| --- | --- | --- |
| `scripts/install.sh` | Installs `web/` + `functions/` npm deps | Once after clone; after any `package.json` change |
| `scripts/dev.sh` | Local Next.js dev server at `http://localhost:3000`. Uses Claude CLI via `web/.env.local`. | Day-to-day local development |
| `scripts/build-firebase.sh` | Builds the static export (`web/out/`). Does **not** deploy. | When you want to inspect the build or run the Firebase emulator |
| `scripts/set-gemini-key.sh` | Stores `GEMINI_KEY` as a Firebase secret (the Cloud Function reads it at runtime — it never enters the JS bundle). Get a key at https://aistudio.google.com/apikey | Once before first deploy; again on key rotation |
| `scripts/deploy-firebase.sh` | Full deploy: builds static site **and** Cloud Function, pushes both to `polygence-pubmed-graphrag.web.app` | Whenever you want to ship the demo publicly |

### First-time setup

```bash
scripts/install.sh                  # deps
# (one-time, only if deploying:)
firebase login                      # sailyn@gmail.com
scripts/set-gemini-key.sh         # paste Gemini key when prompted
```

> **Cloud Functions require the Blaze (pay-as-you-go) plan.** Upgrade
> [in the Firebase console](https://console.firebase.google.com/project/polygence-pubmed-graphrag/usage/details)
> before first deploy. Free-tier quotas (2M invocations / 400k GB-s / month)
> cover the demo so the bill stays at $0 unless it gets pummeled.

### Day-to-day

```bash
scripts/dev.sh                      # local — Claude CLI
scripts/deploy-firebase.sh          # ship to *.web.app — Gemini proxy
```

---

## Two deploy targets, one codebase

| Target | Command | LLM provider | Where the key lives |
| --- | --- | --- | --- |
| **Local dev** | `scripts/dev.sh` | Claude Code CLI (subprocess) | None — uses your Claude Code auth |
| **Firebase Hosting** | `scripts/deploy-firebase.sh` | Gemini via Cloud Function proxy (default) **or** user-supplied OpenAI / Anthropic / Gemini key via in-app modal | `GEMINI_KEY` Firebase secret, server-side only |

Mode is selected at build time by the `NEXT_PUBLIC_BYOK` env var (set
automatically by `build-firebase.sh`). Local dev keeps the Next.js API
routes; the Firebase build is fully static and the browser calls
`/api/llm` (rewritten to the Cloud Function) or the provider directly.

### Request flow on the deployed site

```
user clicks Explain / sends Chat message
        ↓
browser runs retrieval locally over the cached /data/graph.json
        ↓
browser POSTs { systemPrompt, userPrompt } to /api/llm
        ↓  (Hosting rewrite → Cloud Function `ask` in us-central1)
function: checks Origin, per-IP rate limit, reads GEMINI_KEY secret
        ↓
generativelanguage.googleapis.com (Gemini OpenAI-compat) → answer
        ↓
browser renders answer + GraphRAG retrieval strip + "Context sent to model"
```

If the user has pasted their own key in the **API key modal** (key icon in
the right sidebar header), the browser bypasses the proxy and calls
OpenAI / Anthropic / Gemini directly with their key — useful for power
users who want to spend their own credit.

---

## Architecture (agent modules)

| Agent | Where | What it does |
| --- | --- | --- |
| Data Loading | `preprocess/build_graph.py: load_*` | Parses the six TSVs, normalises IDs and aliases. |
| Graph Construction | `preprocess/build_graph.py: build_graph` | Builds the master graph from `clinicalVariants.tsv` (curated backbone) + harvests PMIDs from `relationships.tsv`. |
| Graph Simplification | same file | Drops `not associated` / `ambiguous`, collapses rsIDs into per-gene cluster nodes, injects drug-class nodes, tags critical (1A/1B Toxicity) edges. |
| Interaction | `web/components/GraphCanvas.tsx`, `web/components/LeftSidebar.tsx` | Cytoscape canvas (fcose layout) with drag/zoom/pan, fit/reset/zoom controls, search, filters. |
| Retrieval / Context | `web/lib/graph/retrieve.ts` · pure ops in `web/lib/graph/ops.ts` | **Question-driven retrieval**: extracts entities via n-gram fuzzy match → 1-hop neighbourhoods → BFS shortest paths → structured text packet. Runs server-side locally and in-browser on Firebase. |
| Data facade | `web/lib/graph/data-api.ts` | Single import for `/api/graph` + `/api/search`. Picks server route in dev, in-browser ops in BYOK build. |
| LLM Provider (server) | `web/lib/llm/{index,openai,claude-cli}.ts` | Picks Claude CLI / OpenAI / none from `LLM_PROVIDER`. Used by local dev only. |
| LLM Provider (browser) | `web/lib/llm/{browser,browser-pipeline}.ts` | `proxyAsk()` → `/api/llm` Cloud Function (default); `browserAsk()` → user-key direct call. Used by Firebase build. |
| Explanation | `web/app/api/explain/route.ts` (server) · `lib/llm/browser-pipeline.ts:explainInBrowser` (browser) | Graph-grounded explanations for clicked nodes/edges. |
| Chat | `web/app/api/chat/route.ts` (server) · `lib/llm/browser-pipeline.ts:chatInBrowser` (browser) | GraphRAG chat — entity-linked retrieval, structured prompt, LLM grounded in the retrieved subgraph. |
| Cloud Function | `functions/index.js` | Server-side proxy to Gemini with `GEMINI_KEY` secret, Origin allowlist, per-IP rate limit. |

## What makes it real GraphRAG (not just context-stuffing)

The chat endpoint runs this pipeline per question:

```
question text
   ↓ entity linking  (fuse.js n-gram match against the 3k-node index)
linked entities: [drug:sevoflurane, phenotype:Malignant Hyperthermia]
   ↓ 1-hop neighbourhood per entity + BFS shortest paths between pairs
retrieved subgraph: 23 nodes, 37 edges, 1 critical path
   ↓ render structured prompt (entities / neighbourhoods / paths / PMIDs)
graph context (~5–20 KB)
   ↓ LLM (Claude / Gemini / GPT)
answer that cites L1A evidence, mediating genes, PMIDs
```

The retrieved entities, paths and full context packet are returned to the
UI alongside the answer, so the GraphRAG step is visible and inspectable.

## Building the reasoning graph (one-time / data updates only)

```bash
python3 preprocess/build_graph.py
```

Outputs into `data/` (auto-run by `scripts/dev.sh` and `scripts/build-firebase.sh`
if the files are missing):

| File | Size | Contents |
| --- | --- | --- |
| `graph.json` | ~2 MB | 3,213 nodes / 8,024 edges (full simplified graph) |
| `seed_anesthesia.json` | ~28 KB | 36 nodes / 62 edges (default demo view) |
| `search_index.json` | ~302 KB | autocomplete + entity-linking index |

## Local LLM provider config (`web/.env.local`)

```bash
LLM_PROVIDER=claude-cli   # spawn `claude -p` (uses your Claude Code auth)
# or
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
# or
LLM_PROVIDER=none         # AI off; routes still return retrieved context
```

| Provider | Pros | Cons |
| --- | --- | --- |
| **`claude-cli`** | No API key needed (uses your existing Claude Code auth); inherits Claude Code's tool-use machinery; great quality | ~1.5 s subprocess cold-start per call; single-user (CLI auth races on concurrent requests); won't survive a serverless deploy |
| **`openai`** | Fast (~500 ms), production-deployable, streaming-friendly | Needs a paid OpenAI key |
| **`none`** | Useful for debugging the retrieval layer in isolation | No actual LLM answers — routes return the raw context that *would* be sent |

The provider check happens per-request inside `lib/llm/index.ts:ask()`, so
flipping `LLM_PROVIDER` requires no code change — just a server restart.

## Demo walkthrough

The default load shows the **anesthesia / malignant hyperthermia** subgraph:
7 anesthetic drugs, 2 drug classes, 13 genes (RYR1, CACNA1S, BCHE, …),
collapsed variant clusters, and the central Malignant Hyperthermia
phenotype. Critical 1A/1B toxicity edges are highlighted red.

Try:

1. **Click RYR1** → right panel runs `/api/explain` (local) or
   `explainInBrowser` (Firebase); Claude/Gemini returns a graph-grounded
   summary citing the 46 known variants linked to MH-triggering anesthetics.
   Expand "Context sent to model" to see the full prompt.
2. **Click the red `sevoflurane → RYR1` edge** → evidence level 1A,
   Toxicity role, PMIDs all displayed.
3. **Switch to the Chat tab** and ask:
   - *"Why is sevoflurane risky for malignant hyperthermia?"*
   - *"How does RYR1 connect to malignant hyperthermia?"*
   - *"What does CYP2C9 do for warfarin?"*

   Above each answer you'll see the GraphRAG retrieval strip — which
   entities were matched, which shortest paths were retrieved, and the
   subgraph size that was sent to the LLM.
4. **Search "warfarin"** in the left sidebar → loads its 2-hop neighbourhood
   (CYP2C9, VKORC1, bleeding phenotypes).
5. **(Firebase build only) Click the key icon** in the right-sidebar header →
   paste your own OpenAI / Anthropic / Gemini key to bypass the proxy.

## Notes on the simplification rules

- `relationships.tsv` contains ~50k `not associated` / `ambiguous` rows.
  These are dropped — the demo focuses on the *signal* graph.
- `clinicalVariants.tsv` is the backbone because it is evidence-graded and
  ties drugs → genes → phenotypes directly.
- Variants are clustered per gene by default. The right-panel "Variants"
  list shows all rsIDs in the cluster as clickable dbSNP links.
- Drug-class nodes (Volatile Anesthetics, Depolarizing NMBs, etc.) are
  injected from a small curated map in `DRUG_CLASSES` at the top of
  `preprocess/build_graph.py` — add more there.

## Tech stack

- **Frontend:** Next.js 14 (App Router) · TypeScript · Tailwind · Cytoscape.js (fcose layout) · Zustand · Fuse.js · lucide-react
- **Backend (local):** Next.js API routes · Node `child_process` (Claude CLI spawn) · OpenAI SDK
- **Backend (deployed):** Firebase Hosting (static) + Firebase Cloud Functions v2 (Node 20, Gemini proxy)
- **Preprocessing:** Python 3 stdlib only (no pip deps)
- **Data:** PharmGKB TSV exports (included in repo)
