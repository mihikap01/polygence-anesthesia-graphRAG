# Polygence GraphRAG

An interactive biomedical knowledge-graph web app for pharmacogenomic and
anesthesia-related risk reasoning. Built from the PharmGKB-style TSV
exports already present in this folder.

```
polygence-data/
├── *.tsv                       # raw data (kept as-is)
├── preprocess/build_graph.py   # data-loading + simplification agents
├── data/                       # generated JSON artifacts (gitignore)
├── web/                        # Next.js + TypeScript + Cytoscape app
└── README.md
```

## Architecture (agent modules)

| Agent | Where | What it does |
| --- | --- | --- |
| Data Loading | `preprocess/build_graph.py: load_*` | Parses the six TSVs, normalises IDs and aliases. |
| Graph Construction | same file: `build_graph` | Builds the master pharmacogenomic graph from `clinicalVariants.tsv` (curated backbone) + harvests PMIDs from `relationships.tsv`. |
| Graph Simplification | same file | Drops `not associated` / `ambiguous` rows, collapses rsIDs into one cluster per gene, injects drug-class nodes, tags critical (1A/1B Toxicity) edges. |
| Interaction | `web/components/GraphCanvas.tsx`, `web/components/LeftSidebar.tsx` | Cytoscape canvas (fcose layout) with drag/zoom/pan, fit/reset/zoom controls, search, filters, expand/collapse. |
| Retrieval / Context | `web/lib/graph/retrieve.ts` | Extracts a local subgraph around a node/edge and renders a compact text "context packet" for the LLM. |
| Explanation | `web/app/api/explain/route.ts` | Generates graph-grounded explanations for clicked nodes and edges; falls back to deterministic context dump when no API key is set. |
| Chat | `web/app/api/chat/route.ts` | GraphRAG chat that answers only from the visible graph; cites PMIDs in-line. |

## Setup

### 1. Build the simplified reasoning graph (Python)

Pure stdlib, no pip deps required:

```bash
python3 preprocess/build_graph.py
```

Outputs into `data/`:

- `graph.json` — 3,213 nodes / 8,024 edges (full simplified graph)
- `seed_anesthesia.json` — 36 nodes / 62 edges (default demo view)
- `search_index.json` — autocomplete index across all nodes

### 2. Install web app deps and run

```bash
cd web
cp .env.example .env.local      # then add your OPENAI_API_KEY
npm install
npm run dev
```

Open <http://localhost:3000>.

## Demo walkthrough

The default load shows the **anesthesia / malignant hyperthermia** subgraph —
sevoflurane, halothane, isoflurane, desflurane, enflurane, methoxyflurane,
and succinylcholine, plus their canonical genes (RYR1, CACNA1S, BCHE, …),
collapsed variant clusters, the two drug-class nodes, and the central
Malignant Hyperthermia phenotype. Critical 1A/1B toxicity edges are red.

Try these:

1. Click **RYR1** → right panel explains the gene in graph context and lists
   46 known variants linked to MH-triggering anesthetics.
2. Click the red **sevoflurane → RYR1** edge → see evidence level 1A,
   "Toxicity" role, and PMIDs.
3. Switch to the **Chat** tab and ask:
   _"Why is sevoflurane risky for malignant hyperthermia?"_
4. Search **"warfarin"** in the left sidebar → loads a 2-hop neighbourhood
   centred on warfarin (CYP2C9, VKORC1, bleeding phenotypes).

## Without an OpenAI key

The app still runs. The Explain and Chat panels show the raw graph context
packet that *would* be sent to the LLM, plus all retrieved PMIDs. Useful for
verifying the GraphRAG retrieval layer in isolation.

## Notes on the simplification rules

- `relationships.tsv` contains lots of `not associated` / `ambiguous` rows
  (~50k combined). These are dropped — the demo focuses on the *signal*
  pharmacogenomic graph.
- `clinicalVariants.tsv` is the backbone because it is evidence-graded and
  already ties drugs to genes to phenotypes.
- Variants are clustered per gene by default. The right-panel "Variants"
  list shows all rsIDs in the cluster as PubMed-clickable chips.
- Drug-class nodes (Volatile Anesthetics, Depolarizing NMBs, etc.) are
  injected from a small curated map so the anesthesia subgraph reads
  cleanly. Add more classes in `DRUG_CLASSES` at the top of
  `preprocess/build_graph.py`.
