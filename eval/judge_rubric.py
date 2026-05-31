#!/usr/bin/env python3
"""
Rubric judge — per-answer ratings on Faithfulness, Completeness, Clinical
soundness (1-5 each). Haiku judges Sonnet's outputs against ground truth
(not against arm-specific context — that asymmetry was fixed in v2 of the
plan).

Output: eval/rubric.jsonl, one record per (question_id, arm).
Resumable + parallel like judge.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
QUESTIONS = EVAL / "questions.jsonl"
ANSWERS = EVAL / "answers.jsonl"
RUBRIC = EVAL / "rubric.jsonl"

_LOCK = threading.Lock()

RUBRIC_SYSTEM = """You are a pharmacogenomics evaluator. You will see one question, its gold answer (ground truth from PharmGKB), and one candidate answer. Rate the candidate on three dimensions:

FAITHFULNESS (1-5): does every factual claim in the candidate match the gold / ground truth?
  1 = many fabricated or incorrect claims · 3 = some claims wrong or unverifiable · 5 = all claims supported

COMPLETENESS (1-5): does the candidate cover the key entities, evidence levels, and PMIDs the gold contains?
  1 = misses the central fact · 3 = covers the basics but misses key specifics · 5 = covers gold + reasonable elaboration

CLINICAL SOUNDNESS (1-5): would a pharmacist call this misleading or unsafe?
  1 = misleading / unsafe / fabricated dosing · 3 = vague or incomplete but not wrong · 5 = clinically appropriate, well-hedged

Reply with EXACTLY this format on a single line, then a one-sentence reason:
F=<n> C=<n> CS=<n>
<one-sentence reason>

No preamble. Numbers must be integers 1-5."""


@dataclass
class Rubric:
    question_id: str
    arm: str
    faithfulness: int | None
    completeness: int | None
    clinical_soundness: int | None
    reason: str
    duration_ms: int
    cost_usd: float
    error: str | None = None


RUBRIC_RE = re.compile(r"F\s*=\s*([1-5])\s+C\s*=\s*([1-5])\s+CS\s*=\s*([1-5])", re.I)


def call(question: str, gold: dict, answer: str, model: str = "haiku",
         timeout_s: int = 90) -> tuple[int | None, int | None, int | None, str, int, float, str | None]:
    gold_compact = json.dumps(gold, ensure_ascii=False)
    user = f"""Question: {question}

Gold / ground truth: {gold_compact}

Candidate answer:
{answer}

Ratings: """
    args = [
        "claude", "-p", "--output-format", "json", "--model", model,
        "--append-system-prompt", RUBRIC_SYSTEM,
        "--disallowedTools",
        "Read Write Edit Bash WebSearch WebFetch Agent TaskCreate TaskUpdate TaskList",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=user, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, None, None, "", int((time.time() - t0) * 1000), 0.0, "timeout"
    dur = int((time.time() - t0) * 1000)
    if r.returncode != 0:
        return None, None, None, "", dur, 0.0, f"exit {r.returncode}: {r.stderr[:200]}"
    try:
        j = json.loads(r.stdout)
    except Exception as e:
        return None, None, None, "", dur, 0.0, f"json parse: {e}"
    if j.get("is_error"):
        return None, None, None, "", dur, 0.0, "claude is_error"
    text = (j.get("result") or "").strip()
    m = RUBRIC_RE.search(text)
    if not m:
        return None, None, None, text[:160], j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), "no-rating-pattern"
    f_, c_, cs = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # reason = whatever follows the rating line
    reason = text[m.end():].strip()[:240]
    return f_, c_, cs, reason, j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), None


def latest_successful(answers: list[dict]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for a in answers:
        if a.get("error") or not a.get("answer"):
            continue
        out[(a["question_id"], a["arm"])] = a
    return out


def load_done() -> set[tuple[str, str]]:
    if not RUBRIC.exists():
        return set()
    out = set()
    for ln in RUBRIC.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
            if not j.get("error") and j.get("faithfulness") is not None:
                out.add((j["question_id"], j["arm"]))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--arms", default="A0,A1,A2,A3")
    args = ap.parse_args()

    qs = {json.loads(ln)["id"]: json.loads(ln) for ln in QUESTIONS.read_text().splitlines() if ln.strip()}
    ans_raw = [json.loads(ln) for ln in ANSWERS.read_text().splitlines() if ln.strip()]
    ans = latest_successful(ans_raw)
    arms_set = set(args.arms.split(","))
    done = load_done()

    tasks = []
    for (qid, arm), a in ans.items():
        if arm not in arms_set:
            continue
        if (qid, arm) in done:
            continue
        if qid not in qs:
            continue
        tasks.append((qid, arm, qs[qid], a))
    if args.limit:
        tasks = tasks[:args.limit]

    total = len(ans) - len(done)
    print(f"rubric: {len(tasks)} to do  · model={args.model}  · workers={args.workers}", file=sys.stderr)
    counter = {"n": 0, "cost": 0.0}
    t0 = time.time()

    def work(t):
        qid, arm, q, a = t
        f_, c_, cs, reason, dur, cost, err = call(q["question"], q.get("gold") or {}, a["answer"], model=args.model)
        rec = Rubric(qid, arm, f_, c_, cs, reason, dur, cost, err)
        with _LOCK:
            with RUBRIC.open("a") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            counter["n"] += 1
            counter["cost"] += cost
            n = counter["n"]
            elapsed = time.time() - t0
            rate = n / max(elapsed, 1)
            eta = (len(tasks) - n) / max(rate, 0.001)
            status = ("✗ " + (err or "")[:40]) if err else f"F={f_} C={c_} CS={cs}"
            print(f"[{n}/{len(tasks)}] {qid:>7} {arm}  {dur/1000:4.1f}s  {status}  · eta {eta/60:.1f}m  · ${counter['cost']:.2f}",
                  file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(work, t) for t in tasks]
        for _ in as_completed(futures):
            pass

    print(f"done. {RUBRIC}  · ${counter['cost']:.2f}  · {(time.time()-t0)/60:.1f}m", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
