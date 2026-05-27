# Pre-registration — GraphRAG evaluation

**Frozen on: 2026-05-26**

This file commits the eval's design decisions *before* any expensive runs
are executed, so we cannot move the goalposts after seeing results. Any
changes to the items below after this date must be documented as a
post-hoc deviation in the final writeup.

---

## 1. Generator and judge

- **Generator model:** Claude Opus 4.7 via the Claude Code CLI (`claude -p`).
- **Judge model:** Claude Haiku 4.5 via a separate Claude CLI session.
- **Cross-family alternative considered but not used:** Gemini 2.5 Pro
  was an option but rejected to keep the eval on a single provider.
  Trade-off acknowledged as a threat to validity ("judge self-preference,
  within-Claude").
- **Generation parameters:** temperature 0.2, max_tokens 1024, identical
  across all four arms.

## 2. Arms

| Arm | Spec |
| --- | --- |
| A0 no-context | LLM receives the question only |
| A1 naïve RAG (steelman) | spec below |
| A2 full-context dump | the entire seed subgraph rendered as text |
| A3 GraphRAG | the system as built (`web/lib/graph/retrieve.ts:retrieveForQuestion`) |

### A1 steelman spec (frozen)

- **Chunking:** one chunk per high-evidence clinicalVariants row (denormalised so entity names appear in the chunk text); plus one chunk per relationships.tsv "Associated" row with PMID metadata.
- **Embedder:** `sentence-transformers/all-mpnet-base-v2` (free, local; deviation from the earlier OpenAI plan because we want zero external API keys for the baseline).
- **Retrieval:** BM25 + dense embedding, fused via reciprocal-rank-fusion (RRF, k=60).
- **Top-K:** 20 candidates → reranked by `BAAI/bge-reranker-v2-m3` → top 8 in prompt.
- **Query rewriting:** the same Claude Opus model produces 2 paraphrases; all three queries are searched; results RRF-merged.
- **Prompt template:** identical to A3's outer prompt — only the context block differs.

If A1 cannot be stood up exactly as specified (e.g., the reranker won't run on the test laptop), the eval is paused, the deviation is documented, and the spec is updated *before* generation begins.

## 3. Held-out split

- **Seed:** 42 (HELDOUT_SEED). Re-running `eval/rebuild_heldout.py` reproduces the exact same split.
- **Fraction held out:** 30% of clinicalVariants rows with evidence level in {1A, 1B, 2A, 2B}.
- **Result on freeze date:** 120 held-out rows; reduced graph contains 3,203 nodes / 7,972 edges (vs. 3,213 / 8,024 in the full graph).
- **Held-out facts may still appear in `relationships.tsv` (less curated) — both A1 and A3 have equal access to those redundant rows. Asymmetry is not exploitable.**

## 4. Question set

- **Generator seed:** 7 (GEN_SEED). Re-running `eval/generate_questions.py` reproduces the same questions.
- **Counts (target / actual on freeze date):**
  - S1 (well-known facts): 20 / **20**
  - S2 (specific evidence levels): 25 / **25**
  - S3 (multi-hop): 40 / **40** — drives H2
  - S4 (citation grounding): 25 / **25**
  - S5 (long-tail / niche): 25 / **25**
  - S6 (negative controls): 25 / **25**
  - S7 (out-of-distribution): 15 / **15**
  - S8 (comparative same-class): 25 / **12** — candidate pool shortfall; S8 is descriptive only, not in any hypothesis
- **Total: 187 questions** (vs. target 200).

## 5. Hypotheses (frozen)

- **H1 (primary):** A3 beats A1 on blinded pairwise preference on > **55%** of the 187 questions (sign test, p < 0.05).
- **H2 (multi-hop, descriptive):** On S3, A3 outperforms A1 on entity recall **and** PMID precision, both by ≥ **10 percentage points** (no inference, descriptive only — n=40 is underpowered).
- **H3 (no regression, descriptive):** On S1, A3 metrics are within **5 percentage points** of A0 metrics (descriptive equivalence check).

No further hypotheses will be added post-hoc.

## 6. Metrics

### Rule-based (deterministic Python)

All metrics are well-defined for every arm — no arm gets N/A by construction.

1. **PMID exists** — cited PMID exists in PubMed/`relationships.tsv` (1 if yes, 0 if invented).
2. **PMID correctly attributed** — judged for each existing PMID: does the cited paper support the specific claim it's attached to?
3. **Entity precision / recall** — versus the question's `gold.entities`.
4. **Evidence-level exact match** — for S2 only.
5. **Refusal correctness** — for S6 and S7 only. Correct = matches `gold.should_refuse` / `gold.expected_negative`.

### LLM-judge (Haiku, blinded)

Judge sees only `(question, answer, ground-truth fact)`. Does **not** see arm-specific retrieved context (which would penalise A0 by construction). 1–5 anchored rubric:

- **Faithfulness 1–5** — every claim supported by ground truth?
- **Completeness 1–5** — covers the gold entities and relationships?
- **Clinical soundness 1–5** — would a pharmacist call it misleading?

### Pairwise preference (primary inference)

For each question, judge sees `(question, answer_A, answer_B)` blinded — arm labels stripped, order randomised — and picks preferred (or tie). Two passes:

- **A3 vs A1** — primary headline.
- **A3 vs A0** — secondary.

Sign test at n=187: detects 55% preference with > 0.9 power at α=0.05.

### Hallucination rate (merged segmentation)

The judge does one segmentation pass per question, producing a fixed list of atomic claims spanning the union of all four arms' answers. Each arm is then scored: which of those claims it (a) made, (b) supported. Same denominator across arms.

## 7. Blinding & ordering

- Answers are stored with opaque IDs; arm labels stripped before any grading pass.
- Generation order across arms is randomised per question.
- Before judging, all four arms' answers are minimally normalised: leading bullet markers stripped, citation format `[PMID:xxx]` → `(citation)`. This partially mitigates blinding-via-style; documented as residual threat.

## 8. Pre-flight gates (all must pass before generation)

1. **Question quality** ≥ 85% on manual 0/1 rating of 30 random questions.
2. **Gold-answer verification** ≥ 28/30 manually verified against held-out rows.
3. **A1 sanity** — A1 runs successfully on 5 questions and retrieves plausible chunks.
4. **Judge calibration** — Haiku/human Pearson r ≥ 0.7 on 20 ratings.
5. **This pre-registration file is committed to git** before any of the 800 answers are generated.

If any gate fails, generation pauses and the gate is fixed first.

## 9. Decision rules (frozen)

- **H1 supported** iff A3 wins > 55% of A3-vs-A1 pairwise comparisons, sign test p < 0.05 over n ≥ 150 non-tie decisions.
- **H2 supported** iff on S3: A3 entity recall AND A3 PMID precision each exceed A1's by ≥ 10 pp (descriptive only — not used for the primary headline claim).
- **H3 supported** iff on S1: A3 metrics differ from A0 metrics by < 5 pp (descriptive equivalence).

Any combination of the three outcomes is reportable. A mixed result is the expected outcome and is the strongest writeup.

## 10. Threats to validity (residual after mitigations)

- Training contamination: PharmGKB is public; the LLM has likely seen it.
- Judge self-preference: Haiku judging Opus is within-Claude (partial mitigation only).
- Blinding-via-style: normalisation is partial; structured prose may still leak arm identity.
- Single model family: results may not generalise to GPT or Gemini.
- A1 strawman risk: if the steelman spec isn't realized in code, all H1 claims are invalid.

These are stated in the final writeup explicitly.
