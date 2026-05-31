# Polygence Anesthesia GraphRAG

> An interactive biomedical knowledge-graph web app for pharmacogenomic and
> anesthesia-related risk reasoning, plus a reproducible evaluation framework
> testing whether the graph layer actually helps the LLM.
>
> **Author:** Mihika Pall · Polygence research mentorship program (2026)

### Links

| | |
| --- | --- |
| 🌐 **Live demo** | [polygence-pubmed-graphrag.web.app](https://polygence-pubmed-graphrag.web.app) |
| 📊 **Eval report** | [polygence-pubmed-graphrag.web.app/eval-report](https://polygence-pubmed-graphrag.web.app/eval-report) |
| 🏗 **Architecture page** | [polygence-pubmed-graphrag.web.app/architecture](https://polygence-pubmed-graphrag.web.app/architecture) · [view raw HTML in repo](./architecture.html) |
| 💾 **Source** | [github.com/mihikap01/polygence-anesthesia-graphRAG](https://github.com/mihikap01/polygence-anesthesia-graphRAG) |

---

## What this project is

A research-grade demonstration of **subgraph-RAG over PharmGKB** — the open clinical
pharmacogenomics database — built from scratch and evaluated honestly.

It has two parallel surfaces over the same underlying knowledge graph:

### 1. The interactive web app (the demo)
A Cytoscape-rendered graph (3,213 nodes, 8,024 edges built from PharmGKB
clinical-variant rows) with two grounded reasoning panels:

- **Explain** — click any node or edge on the canvas; an LLM explains it
  using its 2-hop neighbourhood as context.
- **Chat** — type a free-form question; a real GraphRAG retrieval step
  (entity-linking → 1-hop neighbourhoods → BFS shortest paths) runs
  *before* the LLM is called.

Every answer ships with a collapsible **"Context sent to model"** panel
showing the exact system prompt, user prompt, graph context, and token
estimate. The retrieval step is fully observable, not magic.

### 2. The evaluation framework
An end-to-end Python pipeline that tests whether the graph layer
genuinely outperforms (a) a strong plain-text retriever and (b) a no-context
baseline. 187 held-out questions across 8 strata, four independent metric
families (rule-based, blinded pairwise preference, anchored 1–5 rubric
ratings, merged-claim hallucination rate). All scripts and raw evidence
committed for reproducibility.

The full result and methodology live in the
[**eval report**](https://polygence-pubmed-graphrag.web.app/eval-report).
**Headline finding:** the graph layer did not statistically beat
plain-text RAG on this benchmark (49% pairwise preference, p=0.83). The
LLM judge preferred the no-context model's confident-but-fabricating
answers (it invented PMIDs ~68% of the time and the judge couldn't tell).
The graph's one consistent advantage was appropriate refusal on
out-of-distribution queries.

---

## Repo layout

```
polygence-anesthesia-graphRAG/
├── README.md                   This file
├── CLAUDE.md                   Quick reference for AI coding assistants
├── architecture.html           Standalone architecture page (visualises the codebase)
├── eval-explainer.html         Visual one-pager of the eval methodology
├── firebase.json, .firebaserc  Firebase Hosting + Functions config
│
├── *.tsv                       Raw PharmGKB data (kept as-is, ~160k rows total)
├── preprocess/build_graph.py   TSV → graph.json + seed_anesthesia.json + search_index.json
├── data/                       Generated JSON artifacts (gitignored — rerun the script)
│
├── functions/                  Firebase Cloud Function: /api/llm → Gemini proxy
│
├── web/                        Next.js 14 + TS + Cytoscape app
│   ├── app/                    Pages + API routes
│   ├── components/             GraphCanvas, sidebars, modal, UI primitives
│   ├── lib/                    Graph retrieval, LLM provider abstraction, store
│   └── scripts/                Build / deploy scripts (called by repo-root wrappers)
│
├── scripts/                    One-script-per-task wrappers (run from anywhere)
│
└── eval/                       Reproducible evaluation framework
    ├── preregistration.md      Frozen hypotheses + decision rules
    ├── *.py                    Pipeline: rebuild, generate, run, grade, judge, segment, report
    ├── questions.jsonl         187 held-out questions + gold records
    ├── answers.jsonl           748 LLM responses (raw evidence, committed)
    ├── judgments.jsonl         374 pairwise judgments
    ├── scores.jsonl            Rule-based metrics
    ├── rubric.jsonl            F/C/CS ratings
    ├── segments.jsonl          Merged-claim records
    └── report.html, results.json
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

- **Frontend:** Next.js 14 (App Router) · TypeScript · Tailwind · Cytoscape.js (fcose layout) · Zustand · Fuse.js · Radix UI Dialog/Slot · CVA · lucide-react
- **Backend (local):** Next.js API routes · Node `child_process` spawning `claude -p` (the Claude Code CLI)
- **Backend (deployed):** Firebase Hosting (static export) + Firebase Cloud Functions v2 (Node 20, Gemini proxy with secret-managed key)
- **Preprocessing:** Python 3 stdlib only (no pip deps)
- **Eval pipeline:** Python 3.12 · `rank_bm25` · `sentence-transformers` (all-MiniLM-L6-v2, runs locally) · `rapidfuzz` · `numpy` · Claude CLI for generation and judging
- **Data:** PharmGKB TSV exports (included in repo, ~160k rows across 6 files)
- **Eval models:** Claude Sonnet 4 (generator) · Claude Haiku 4.5 (judge, within-Claude size split)
- **Production LLM:** Gemini 2.5 Flash (Cloud Function default) · user-supplied OpenAI / Anthropic / Gemini / DeepSeek key (in-app BYOK modal)

---

## Reproducing the evaluation

Everything needed to reproduce the eval from scratch is committed under `eval/`.
Total wall time: ~5 hours. Total LLM-cost (Claude): ~$45.

```bash
# 1. Build the held-out graph + 187 questions (deterministic, seeds 42 + 7)
python3 eval/rebuild_heldout.py
python3 eval/generate_questions.py

# 2. Build A1's plain-text-RAG index (one-time, ~20s, no LLM cost)
python3 eval/a1_index.py

# 3. Generate the 4 × 187 = 748 answers via the Claude CLI
#    (~3-4 hours, ~$25, resumable — re-run skips completed)
python3 eval/run.py --model sonnet

# 4. Rule-based metrics (deterministic, no LLM)
python3 eval/grade.py

# 5. Blinded pairwise preference (Haiku judge, 6 parallel workers)
python3 eval/judge.py --workers 6

# 6. Anchored rubric ratings (F/C/CS 1-5)
python3 eval/judge_rubric.py --workers 8

# 7. Merged-claim hallucination
python3 eval/segment.py --workers 4

# 8. Generate the final HTML report + machine-readable summary
python3 eval/report.py
open eval/report.html
```

All seeds are fixed; outputs are identical on re-run. The full preregistration
(hypotheses, decision thresholds, A1 spec) lives in `eval/preregistration.md`
and was committed before any LLM calls were made.

---

## Acknowledgements

This project was built by **Mihika Pall** as part of the
[Polygence](https://www.polygence.org/) research mentorship program (2026).

- **Data:** [PharmGKB](https://www.pharmgkb.org/), the open clinical pharmacogenomics
  knowledge base. All curated drug-gene-variant-phenotype relationships and
  evidence levels are sourced from PharmGKB's released TSV exports.
- **Anesthesia/MH expertise:** the demo's anesthesia seed subgraph and drug-class
  injections were informed by CPIC and MHAUS guidelines.
- **Engineering assistance:** development and evaluation work were paired with
  [Claude Code](https://claude.com/claude-code) (Claude Opus 4.7).

---

## License

The source code in this repository is released under the MIT License.
PharmGKB data is governed by [PharmGKB's data use policy](https://www.pharmgkb.org/page/dataUsagePolicy).
