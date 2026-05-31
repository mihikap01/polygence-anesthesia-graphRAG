#!/usr/bin/env python3
"""
Merged-claim segmentation for the hallucination metric.

For each question, one Haiku call sees ALL FOUR answers (blinded as A/B/C/D)
and ground truth, extracts the union of atomic factual claims across all
four, then for each (claim, arm) marks whether the arm MADE the claim and
whether the claim is SUPPORTED by ground truth.

This is the only fair way to compute hallucination rate across arms (per
preregistration §6): same denominator (the same merged claim list) for
every arm. Per-arm hallucination = unsupported claims made / claims made.

Output: eval/segments.jsonl (one record per question).
Resumable + parallel.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
QUESTIONS = EVAL / "questions.jsonl"
ANSWERS = EVAL / "answers.jsonl"
SEGMENTS = EVAL / "segments.jsonl"

_LOCK = threading.Lock()

SEGMENT_SYSTEM = """You are an evaluator for pharmacogenomic claims. You will see one question, its gold/ground-truth, and four candidate answers labeled A, B, C, D. Your job is in two steps:

1) EXTRACT all distinct atomic factual claims that appear across any of the four answers. An "atomic claim" is one verifiable statement (e.g. "CYP2C9*3 has 1A evidence for warfarin response"). Number them c1, c2, ... Aim for 5-15 claims per question; merge near-duplicates.

2) For each claim, output (a) which subset of {A,B,C,D} MADE that claim, and (b) whether the claim is SUPPORTED by gold/ground truth (true/false/unverifiable).

Reply with STRICTLY valid JSON, no preamble, in this exact schema:
{
  "claims": [
    {"id": "c1", "text": "...", "made_by": ["A","C"], "supported": "true"},
    {"id": "c2", "text": "...", "made_by": ["B","D"], "supported": "false"},
    ...
  ]
}

Use "true" / "false" / "unverifiable" for `supported` — strings, not booleans.
No commentary. Just the JSON object."""


@dataclass
class Segments:
    question_id: str
    label_to_arm: dict[str, str]   # {"A": "A2", "B": "A0", ...}
    claims: list[dict]             # raw from judge
    duration_ms: int
    cost_usd: float
    error: str | None = None


def latest_successful(answers: list[dict]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for a in answers:
        if a.get("error") or not a.get("answer"):
            continue
        out[(a["question_id"], a["arm"])] = a
    return out


def load_done() -> set[str]:
    if not SEGMENTS.exists():
        return set()
    out = set()
    for ln in SEGMENTS.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
            if not j.get("error") and j.get("claims"):
                out.add(j["question_id"])
        except Exception:
            pass
    return out


def extract_json(text: str) -> dict | None:
    """Pull a JSON object from a model response. Tolerates code-fences + preamble."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    # find first { ... balanced } at top level
    depth = 0; start = -1
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blob = t[start:i+1]
                try:
                    return json.loads(blob)
                except Exception:
                    return None
    return None


def call(question: str, gold: dict, labeled: list[tuple[str, str, str]],
         model: str = "haiku", timeout_s: int = 120) -> tuple[list[dict], int, float, str | None]:
    """labeled = list of (label, arm, answer_text). Label is what the judge sees."""
    gold_compact = json.dumps(gold, ensure_ascii=False)
    blocks = []
    for label, _arm, text in labeled:
        blocks.append(f"=== Answer {label} ===\n{text}\n")
    user = f"""Question: {question}

Gold / ground truth: {gold_compact}

{"".join(blocks)}

Return the JSON now."""
    args = [
        "claude", "-p", "--output-format", "json", "--model", model,
        "--append-system-prompt", SEGMENT_SYSTEM,
        "--disallowedTools",
        "Read Write Edit Bash WebSearch WebFetch Agent TaskCreate TaskUpdate TaskList",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=user, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return [], int((time.time()-t0)*1000), 0.0, "timeout"
    dur = int((time.time()-t0)*1000)
    if r.returncode != 0:
        return [], dur, 0.0, f"exit {r.returncode}: {r.stderr[:200]}"
    try:
        j = json.loads(r.stdout)
    except Exception as e:
        return [], dur, 0.0, f"outer-json parse: {e}"
    if j.get("is_error"):
        return [], dur, 0.0, "claude is_error"
    text = (j.get("result") or "").strip()
    payload = extract_json(text)
    if not payload or "claims" not in payload:
        return [], j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), f"no-json: {text[:120]}"
    claims = payload["claims"]
    if not isinstance(claims, list):
        return [], j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), "claims-not-list"
    return claims, j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    qs = {json.loads(ln)["id"]: json.loads(ln) for ln in QUESTIONS.read_text().splitlines() if ln.strip()}
    ans = latest_successful([json.loads(ln) for ln in ANSWERS.read_text().splitlines() if ln.strip()])
    done = load_done()
    rng = random.Random(args.seed)

    qids = [qid for qid in sorted(qs) if qid not in done]
    # Require all 4 arms present
    qids = [qid for qid in qids if all((qid, a) in ans for a in ("A0","A1","A2","A3"))]
    if args.limit:
        qids = qids[:args.limit]

    print(f"segment: {len(qids)} questions to do · model={args.model} · workers={args.workers}", file=sys.stderr)
    counter = {"n": 0, "cost": 0.0}
    t0 = time.time()

    def work(qid):
        # Shuffle arms → blind labels
        arms = ["A0","A1","A2","A3"]
        rng_local = random.Random(args.seed + hash(qid) % 10_000)
        rng_local.shuffle(arms)
        labels = ["A","B","C","D"]
        label_to_arm = dict(zip(labels, arms))
        labeled = [(lbl, arm, ans[(qid, arm)]["answer"]) for lbl, arm in zip(labels, arms)]
        claims, dur, cost, err = call(qs[qid]["question"], qs[qid].get("gold") or {}, labeled, model=args.model)
        rec = Segments(qid, label_to_arm, claims, dur, cost, err)
        with _LOCK:
            with SEGMENTS.open("a") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            counter["n"] += 1; counter["cost"] += cost
            n = counter["n"]
            elapsed = time.time() - t0
            rate = n / max(elapsed, 1)
            eta = (len(qids) - n) / max(rate, 0.001)
            status = ("✗ " + (err or "")[:50]) if err else f"{len(claims)} claims"
            print(f"[{n}/{len(qids)}] {qid:>7}  {dur/1000:5.1f}s  {status}  · eta {eta/60:.1f}m  · ${counter['cost']:.2f}",
                  file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(work, qid) for qid in qids]
        for _ in as_completed(futures):
            pass

    print(f"done. {SEGMENTS}  · ${counter['cost']:.2f}  · {(time.time()-t0)/60:.1f}m", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
