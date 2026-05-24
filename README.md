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
└── README.md
```

## Architecture (agent modules)

| Agent | Where | What it does |
| --- | --- | --- |
| Data Loading | `preprocess/build_graph.py: load_*` | Parses the six TSVs, normalises IDs and aliases. |
| Graph Construction | `preprocess/build_graph.py: build_graph` | Builds the master graph from `clinicalVariants.tsv` (curated backbone) + harvests PMIDs from `relationships.tsv`. |
| Graph Simplification | same file | Drops `not associated` / `ambiguous`, collapses rsIDs into per-gene cluster nodes, injects drug-class nodes, tags critical (1A/1B Toxicity) edges. |
| Interaction | `web/components/GraphCanvas.tsx`, `web/components/LeftSidebar.tsx` | Cytoscape canvas (fcose layout) with drag/zoom/pan, fit/reset/zoom controls, search, filters. |
| Retrieval / Context | `web/lib/graph/retrieve.ts` | **Question-driven retrieval**: extracts entities via n-gram fuzzy match → 1-hop neighbourhoods → BFS shortest paths → structured text packet. |
| LLM Provider | `web/lib/llm/{index,openai,claude-cli}.ts` | Provider abstraction: swap between Claude Code CLI, OpenAI, or no-LLM via `LLM_PROVIDER` env var. |
| Explanation | `web/app/api/explain/route.ts` | Graph-grounded explanations for clicked nodes/edges. |
| Chat | `web/app/api/chat/route.ts` | GraphRAG chat — entity-linked retrieval, structured prompt, LLM grounded in the retrieved subgraph. |

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
   ↓ LLM (Claude or GPT)
answer that cites L1A evidence, mediating genes, PMIDs
```

The retrieved entities, paths and full context packet are returned to the
UI alongside the answer, so the GraphRAG step is visible and inspectable.

## Setup

### 1. Build the simplified reasoning graph (Python, stdlib only)

```bash
python3 preprocess/build_graph.py
```

Outputs into `data/`:

| File | Size | Contents |
| --- | --- | --- |
| `graph.json` | ~2 MB | 3,213 nodes / 8,024 edges (full simplified graph) |
| `seed_anesthesia.json` | ~28 KB | 36 nodes / 62 edges (default demo view) |
| `search_index.json` | ~302 KB | autocomplete + entity-linking index |

### 2. Install + run the web app

```bash
cd web
cp .env.example .env.local        # edit to pick a provider
npm install
npm run dev
```

Open <http://localhost:3000>.

## LLM provider — three options

`web/.env.local`:

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

1. **Click RYR1** → right panel runs `/api/explain`; Claude returns a graph-
   grounded summary citing the 46 known variants linked to MH-triggering
   anesthetics. Expand "Context sent to model" to see the full prompt.
2. **Click the red `sevoflurane → RYR1` edge** → evidence level 1A,
   Toxicity role, PMIDs all displayed.
3. **Switch to the Chat tab** and ask:
   - *"Why is sevoflurane risky for malignant hyperthermia?"*
   - *"How does RYR1 connect to malignant hyperthermia?"*
   - *"What does CYP2C9 do for warfarin?"*

   Above each answer you'll see the GraphRAG retrieval strip — which
   entities were matched, which shortest paths were retrieved, and the
   subgraph size that was sent to Claude.
4. **Search "warfarin"** in the left sidebar → loads its 2-hop neighbourhood
   (CYP2C9, VKORC1, bleeding phenotypes).

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
- **Backend:** Next.js API routes · Node `child_process` (Claude CLI spawn) · OpenAI SDK
- **Preprocessing:** Python 3 stdlib only (no pip deps)
- **Data:** PharmGKB TSV exports (included in repo)
