#!/usr/bin/env python3
"""
Driver: for each question in eval/questions.jsonl, run all 4 arms via Claude
CLI and append answers to eval/answers.jsonl.

Resumable: if (question_id, arm) is already in answers.jsonl, skip it.

Usage:
  python3 eval/run.py                       # run all
  python3 eval/run.py --limit 5             # pilot first 5 questions
  python3 eval/run.py --only A0,A3          # only some arms
  python3 eval/run.py --strata S3,S6        # only some strata
  python3 eval/run.py --model sonnet        # cheaper model for a dry run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

# allow `from arms import ...` when run as script
sys.path.insert(0, str(Path(__file__).parent))

from arms import run_a0, run_a1, run_a2, run_a3, ArmAnswer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
QUESTIONS = EVAL / "questions.jsonl"
ANSWERS = EVAL / "answers.jsonl"


def load_questions() -> list[dict]:
    return [json.loads(ln) for ln in QUESTIONS.read_text().splitlines() if ln.strip()]


def load_done() -> set[tuple[str, str]]:
    if not ANSWERS.exists():
        return set()
    out: set[tuple[str, str]] = set()
    for ln in ANSWERS.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
            if j.get("answer") and not j.get("error"):  # only count successful answers
                out.add((j["question_id"], j["arm"]))
        except Exception:
            continue
    return out


def append_answer(a: ArmAnswer) -> None:
    with ANSWERS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = no limit")
    ap.add_argument("--only", default="A0,A1,A2,A3", help="comma-separated arms")
    ap.add_argument("--strata", default="", help="comma-separated strata filter")
    ap.add_argument("--model", default="opus", help="claude model")
    args = ap.parse_args()

    arms = set(args.only.split(","))
    strata = set(args.strata.split(",")) if args.strata else None

    qs = load_questions()
    if strata:
        qs = [q for q in qs if q["stratum"] in strata]
    if args.limit:
        qs = qs[:args.limit]

    done = load_done()
    print(f"questions: {len(qs)}, already done: {len(done)}, arms: {sorted(arms)}", file=sys.stderr)
    print(f"model: {args.model}", file=sys.stderr)

    # Lazy-load expensive globals once
    gd = None
    seed_graph = None
    if "A3" in arms:
        from retrieve_py import load_heldout
        gd = load_heldout()
        print(f"A3: loaded held-out graph ({len(gd.nodes)} nodes)", file=sys.stderr)
    if "A2" in arms:
        from retrieve_py import load_seed
        seed_graph = load_seed()
        print(f"A2: loaded seed subgraph ({len(seed_graph['nodes'])} nodes)", file=sys.stderr)

    total = len(qs) * len(arms)
    progress = len(done)
    t0 = time.time()
    cost_total = 0.0

    for q in qs:
        qid = q["id"]
        for arm in sorted(arms):  # deterministic order
            if (qid, arm) in done:
                continue
            try:
                if arm == "A0":
                    a = run_a0(qid, q["question"], model=args.model)
                elif arm == "A1":
                    a = run_a1(qid, q["question"], model=args.model)
                elif arm == "A2":
                    a = run_a2(qid, q["question"], seed_graph, model=args.model)
                elif arm == "A3":
                    a = run_a3(qid, q["question"], gd, model=args.model)
                else:
                    print(f"unknown arm: {arm}", file=sys.stderr)
                    continue
            except KeyboardInterrupt:
                print("\ninterrupted; partial progress saved to answers.jsonl", file=sys.stderr)
                return 130
            except Exception as e:
                a = ArmAnswer(arm, qid, "", 0, 0.0, f"exception: {e}", 0)

            append_answer(a)
            progress += 1
            cost_total += a.cost_usd
            elapsed = time.time() - t0
            rate = progress / max(elapsed, 1)
            eta_s = (total - progress) / max(rate, 0.001)
            status = "✗ " + (a.error or "")[:60] if a.error else f"✓ {len(a.answer)} chars"
            print(
                f"[{progress}/{total}] {qid:>7} {arm}  {a.duration_ms/1000:5.1f}s  "
                f"${a.cost_usd:6.4f}  {status}  · eta {eta_s/60:.1f}min  · ${cost_total:.2f} total",
                file=sys.stderr,
            )

    print(f"done. answers in {ANSWERS}  · total cost ${cost_total:.2f}  · elapsed {(time.time()-t0)/60:.1f}min",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
