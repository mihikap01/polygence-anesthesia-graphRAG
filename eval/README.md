# GraphRAG Eval

An end-to-end evaluation comparing the GraphRAG pipeline against three
baselines on a held-out subset of PharmGKB clinical-variant facts.

**Status: Phase A complete.** Phase B (sanity checks) and Phase C (generation
+ grading + report) are not yet implemented.

See `~/.claude/plans/elegant-skipping-quiche.md` for the full plan and
`../eval-explainer.html` for a visual walkthrough.

---

## What's in this directory

| File | Purpose | Status |
| --- | --- | --- |
| `rebuild_heldout.py` | Deterministic 30% split of high-evidence rows; rebuilds the graph without them. Reads `clinicalVariants.tsv`, writes `eval/heldout_variants.tsv` + `data/graph_heldout.json`. | ✅ |
| `generate_questions.py` | Generates ~200 questions from held-out rows across 8 strata. Writes `eval/questions.jsonl`. | ✅ |
| `heldout_variant_hashes.txt` | One row-hash per line (consumed by `preprocess/build_graph.py` via `HELDOUT_VARIANTS` env var). | ✅ generated |
| `heldout_variants.tsv` | The 120 held-out clinicalVariants rows in their original TSV form. | ✅ generated |
| `questions.jsonl` | 187 questions, one JSON object per line, with `gold` records. | ✅ generated |
| `preregistration.md` | Frozen design decisions, hypotheses, metric definitions, decision rules. Read before changing anything below. | ✅ |
| `baselines/a1_steelman.py` | The naïve-RAG (A1) baseline — embedding + reranker + RRF. | ⏳ Phase C |
| `run.py` | Driver: 4 arms × 187 questions → `eval/answers.jsonl`. | ⏳ Phase C |
| `grade.py` | Rule-based metrics + Haiku-judge calls → `eval/scores.jsonl`. | ⏳ Phase C |
| `segment.py` | Merged-claim segmentation pass for hallucination metric. | ⏳ Phase C |
| `report.py` | Final headline + per-stratum + failure-mode tables. | ⏳ Phase C |

---

## How to reproduce Phase A (what's done)

```bash
# 1. Build the reduced graph (120 rows held out, deterministic)
python3 eval/rebuild_heldout.py

# 2. Generate 187 questions with gold answers (deterministic)
python3 eval/generate_questions.py

# 3. Inspect a few samples (sanity check by eye)
python3 -c "
import json
for ln in list(open('eval/questions.jsonl'))[:5]:
    q = json.loads(ln)
    print(q['stratum'], '·', q['question'])
    print('  gold:', q['gold'].get('answer_summary', '...'))
    print()
"
```

Both scripts are idempotent and produce identical output on every run
(seeds 42 for held-out, 7 for questions).

---

## Phase B — pre-flight gates (next)

Per `preregistration.md` §8, all five gates must pass before any
LLM generation runs:

1. Question quality ≥ 85% on manual 0/1 rating of 30 random questions.
2. Gold-answer verification on the same 30 (≥ 28 must check out).
3. A1 (naïve RAG) sanity — runs on 5 questions, retrieves plausible chunks.
4. Judge (Haiku) calibration vs. human on 20 ratings, Pearson r ≥ 0.7.
5. This pre-registration committed to git.

Gates 1, 2, and 5 are humans-in-the-loop; gates 3 and 4 will be scripted
when Phase C is built.

---

## Phase C — what's not yet built

In rough order:

1. **`baselines/a1_steelman.py`** — chunker over the TSVs, sentence-transformers
   embedder, BM25, RRF fusion, BGE reranker. Spec is frozen in
   `preregistration.md` §2.
2. **`run.py`** — for each question, generate 4 answers (one per arm),
   write to `eval/answers.jsonl` in randomised arm order. Uses the
   existing Claude Code CLI for A0/A2/A3 and the same CLI for A1
   (with the RAG context prepended).
3. **`grade.py`** — runs the rule-based metrics deterministically, then
   calls Haiku for the rubric metrics + pairwise preference. Writes
   `eval/scores.jsonl`.
4. **`segment.py`** — for each question, segment all four answers'
   claims into a merged atomic-claim list. One pass per question.
5. **`report.py`** — produces `eval/report.html` with the headline
   table, per-stratum table, failure-mode chart, and 5–10 illustrative
   examples.

---

## Conventions

- **All paths are relative to the repo root.** Run scripts from there:
  `python3 eval/<script>.py`.
- **Determinism is enforced via env vars:** `HELDOUT_SEED` (default 42),
  `GEN_SEED` (default 7). Don't change them without documenting in the
  writeup as a deviation.
- **Held-out hashes are stable across reorderings** of TSV columns — see
  `rebuild_heldout.py:row_hash`.
- **The reduced graph at `data/graph_heldout.json` is what A3 and A1 use.**
  The full graph at `data/graph.json` is for the live web app and is left
  untouched.

---

## Why this design? (TL;DR — see plan for the long version)

- **Held-out split** so neither A3 nor A1 has memorised the test facts.
- **A1 is a steelman** because beating a weak text-similarity baseline
  proves nothing about graph structure.
- **Primary inference is the n=187 pairwise preference test** — the only
  adequately-powered test in the design.
- **Cross-family judging would be stronger** than Claude-judging-Claude;
  we use within-Claude (Opus generates, Haiku judges) because the user
  chose to stay on one provider. The threat is documented.
- **A mixed result is the expected outcome** and is the strongest writeup.
