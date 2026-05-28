#!/usr/bin/env python3
"""
Rule-based grading pass over eval/answers.jsonl.

For each answer, computes deterministic metrics symmetric across arms:
  - cited_pmids:           PMIDs found in the answer text
  - pmid_exists:           fraction of cited PMIDs that exist in our corpus
  - pmid_correct (S4):     fraction of cited PMIDs that match the question's gold PMID set
  - entity_recall:         fraction of gold entities mentioned in the answer
  - entity_precision_lite: heuristic — penalises long answers that mention many entities not in gold
  - evidence_level_match (S2): 1 if claimed level matches gold, 0 otherwise, null if no claim
  - refusal_correct (S6, S7): 1 if behavior matches gold's should_refuse / expected_negative

Output: eval/scores.jsonl — one record per answer.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
RELS = ROOT / "relationships.tsv"

PMID_RE = re.compile(r"(?:PMID[:\s]?|\bpmid[:\s]?|\bpubmed[/:]?\s?)?\b(\d{6,9})\b", re.I)
# More targeted: only count PMIDs that appear in a citation-y context
PMID_CITATION_RE = re.compile(r"\bPMID[:\s]*(\d{6,9})\b|\[(\d{6,9})\]", re.I)
LEVEL_RE = re.compile(r"\b(L[\s-]?)?(1A|1B|2A|2B|3|4)\b(?:\s*evidence|\s*level)?", re.I)
LEVEL_TAG_RE = re.compile(r"\blevel\s*[:=]?\s*(1A|1B|2A|2B|3|4)\b", re.I)


def load_all_pmids() -> set[str]:
    """Every PMID that appears in relationships.tsv — proxy for 'PMID exists in corpus'."""
    out: set[str] = set()
    csv.field_size_limit(sys.maxsize)
    with RELS.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for r in reader:
            for p in re.split(r"[,;]", r.get("PMIDs") or ""):
                p = p.strip()
                if p.isdigit():
                    out.add(p)
    return out


def extract_pmids(text: str) -> list[str]:
    """Pull PMID-like ids that appear in a citation context."""
    found = []
    for m in PMID_CITATION_RE.finditer(text):
        pmid = m.group(1) or m.group(2)
        if pmid and pmid not in found:
            found.append(pmid)
    return found


def extract_levels(text: str) -> list[str]:
    """Extract evidence level claims (1A, 1B, 2A, 2B, 3, 4). Returns the set claimed."""
    levels = set()
    for m in LEVEL_TAG_RE.finditer(text):
        levels.add(m.group(1).upper())
    for m in LEVEL_RE.finditer(text):
        # Be lenient about word boundaries — only count if preceded by 'level', 'L', or comma-near
        # Simple heuristic: require a nearby "evidence" or "level" within 20 chars.
        ctx = text[max(0, m.start()-20):m.end()+20].lower()
        if "evidence" in ctx or "level" in ctx or " l" in " "+ctx:
            levels.add(m.group(2).upper())
    return sorted(levels)


def is_refusal(text: str) -> bool:
    """Heuristic: does the answer indicate 'no information / not in graph / refuses to answer'?"""
    t = text.lower()
    cues = [
        "not in pharmgkb", "not in the graph", "not in this graph",
        "no pharmacogenomic guideline", "no guideline", "no information",
        "no entries", "no results", "no associations", "no data",
        "cannot find", "could not find", "no specific guidance",
        "does not contain", "does not have", "is not present",
        "i don't have", "i do not have", "no retrieved context",
        "there is no", "there are no", "outside", "i'm unable",
        "no specific pharmacogenomic", "do not appear",
    ]
    return any(c in t for c in cues)


def is_negative_response(text: str) -> bool:
    """For S6: 'no, not associated / no clinically meaningful association'."""
    t = text.lower()
    cues = [
        "no clinically meaningful", "not clinically meaningful",
        "no clinically significant", "no significant",
        "no, ", "no.\n", "no association", "not associated",
        "no documented", "no curated evidence",
        "ambiguous", "not listed as associated",
    ]
    return any(c in t for c in cues)


def mentions_entity(text: str, entity: str) -> bool:
    """Case-insensitive whole-token match — handles 'CYP2C9*1' style + general names."""
    if not entity:
        return False
    pattern = re.compile(rf"\b{re.escape(entity)}\b", re.I)
    return bool(pattern.search(text))


def grade_one(answer_rec: dict, q: dict, corpus_pmids: set[str]) -> dict:
    text = answer_rec.get("answer") or ""
    arm = answer_rec.get("arm")
    qid = answer_rec.get("question_id")
    stratum = q.get("stratum", "?")
    gold = q.get("gold", {}) or {}

    metrics: dict = {
        "question_id": qid,
        "stratum": stratum,
        "arm": arm,
        "answer_chars": len(text),
        "context_chars": answer_rec.get("context_chars", 0),
        "duration_ms": answer_rec.get("duration_ms", 0),
        "error": answer_rec.get("error"),
    }
    if not text or answer_rec.get("error"):
        return metrics

    # ------- entity precision / recall --------
    gold_entities = gold.get("entities") or []
    mentioned = [e for e in gold_entities if mentions_entity(text, e)]
    metrics["entity_recall"] = (len(mentioned) / len(gold_entities)) if gold_entities else None
    metrics["entities_mentioned"] = mentioned
    metrics["entities_missed"] = [e for e in gold_entities if e not in mentioned]

    # ------- PMID metrics --------
    cited = extract_pmids(text)
    metrics["pmids_cited"] = cited
    if cited:
        existing = [p for p in cited if p in corpus_pmids]
        metrics["pmid_exists_rate"] = len(existing) / len(cited)
    else:
        metrics["pmid_exists_rate"] = None
    if stratum == "S4":
        gold_pmids = set(gold.get("pmids") or [])
        if cited:
            correct = [p for p in cited if p in gold_pmids]
            metrics["pmid_correct_rate"] = len(correct) / len(cited)
        else:
            metrics["pmid_correct_rate"] = 0.0   # asked for a citation, gave none

    # ------- evidence-level exact match (S2) --------
    if stratum == "S2" and gold.get("evidence_level"):
        levels = extract_levels(text)
        if levels:
            metrics["evidence_level_match"] = int(gold["evidence_level"].upper() in levels)
            metrics["evidence_levels_claimed"] = levels
        else:
            metrics["evidence_level_match"] = None
            metrics["evidence_levels_claimed"] = []

    # ------- refusal correctness (S6, S7) --------
    if stratum == "S7":
        # Should refuse / say "not in PharmGKB"
        metrics["refusal_correct"] = int(is_refusal(text))
    elif stratum == "S6":
        # Should answer "no association" (expected_negative)
        metrics["refusal_correct"] = int(is_negative_response(text) or is_refusal(text))

    return metrics


def main() -> int:
    answers_path = EVAL / "answers.jsonl"
    questions_path = EVAL / "questions.jsonl"
    out_path = EVAL / "scores.jsonl"
    if not answers_path.exists():
        print("no answers.jsonl yet — run eval/run.py first", file=sys.stderr)
        return 1
    qs = {json.loads(ln)["id"]: json.loads(ln) for ln in questions_path.read_text().splitlines() if ln.strip()}

    print("loading corpus PMID set...", file=sys.stderr)
    corpus_pmids = load_all_pmids()
    print(f"  {len(corpus_pmids):,} unique PMIDs in relationships.tsv", file=sys.stderr)

    answers_raw = [json.loads(ln) for ln in answers_path.read_text().splitlines() if ln.strip()]
    # Dedupe: keep the LAST non-errored answer per (qid, arm). Retries from
    # a resumed run.py append new entries, so the latest non-error is canonical.
    by_key: dict[tuple[str, str], dict] = {}
    for a in answers_raw:
        key = (a.get("question_id", ""), a.get("arm", ""))
        if a.get("error") or not a.get("answer"):
            # keep the error only if we haven't seen a success
            if key not in by_key:
                by_key[key] = a
        else:
            by_key[key] = a   # success overwrites any prior error or earlier success
    answers = list(by_key.values())
    print(f"grading {len(answers)} unique (question, arm) pairs (from {len(answers_raw)} raw rows)...",
          file=sys.stderr)

    scores: list[dict] = []
    for a in answers:
        q = qs.get(a["question_id"])
        if not q:
            continue
        scores.append(grade_one(a, q, corpus_pmids))

    with out_path.open("w", encoding="utf-8") as fh:
        for s in scores:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"wrote {len(scores)} scores to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
