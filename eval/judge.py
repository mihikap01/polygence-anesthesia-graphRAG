#!/usr/bin/env python3
"""
Pairwise preference judge.

The headline test of the eval (per preregistration §6): for each question,
Claude Haiku 4.5 (separate CLI session) sees the question + answers from
two arms, blinded, and picks the better one.

Two comparisons per question:
  - A3 vs A1 — primary (does graph structure beat strong retrieval?)
  - A3 vs A0 — secondary (does the system beat the model alone?)

Blinding:
  - Arm labels stripped from answers
  - Answers minimally normalised (citation format, leading bullets)
  - Order randomised per question (A or B)
  - Judge does not see retrieved context

Output: eval/judgments.jsonl — one record per pairwise comparison
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
from dataclasses import asdict, dataclass
from pathlib import Path

_WRITE_LOCK = threading.Lock()

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
QUESTIONS = EVAL / "questions.jsonl"
ANSWERS = EVAL / "answers.jsonl"
JUDGMENTS = EVAL / "judgments.jsonl"

JUDGE_SYSTEM = """You are an expert pharmacogenomics evaluator. You will see a question and two answers (A and B) from different systems. Pick which answer better serves a clinical/research user.

Criteria (in order of importance):
1. Factual correctness vs. PharmGKB clinical guidelines
2. Specificity (claims real evidence levels, names correct entities, cites real PMIDs)
3. Appropriate epistemic stance (refuses when uncertain, hedges where appropriate)
4. Clinical soundness (does not mislead a reader who would act on it)
5. Style is NOT a criterion — ignore differences in formatting

Reply with EXACTLY one of: "A" or "B" or "TIE", on its own line. Then a single sentence explaining why. No preamble."""


@dataclass
class Judgment:
    question_id: str
    pair: str          # "A3_vs_A1" or "A3_vs_A0"
    a_arm: str         # actual arm shown as "A"
    b_arm: str         # actual arm shown as "B"
    pick: str          # "A" | "B" | "TIE"
    target_pick: str   # "A3" | other arm | "TIE"
    reason: str
    duration_ms: int
    cost_usd: float
    error: str | None = None


# Minimal style normalization (mitigates "structured prose" tell)
def normalize(text: str) -> str:
    t = text or ""
    # citation format [PMID:xxx] → (cite xxx)
    t = re.sub(r"\[PMID[:\s]*(\d+)\]", r"(cite \1)", t, flags=re.I)
    t = re.sub(r"\bPMID[:\s]*(\d+)\b", r"(cite \1)", t, flags=re.I)
    # leading bullet markers
    t = re.sub(r"^\s*[-•·*]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    # bold/italic markers (structured prose often uses **)
    t = t.replace("**", "").replace("__", "")
    # collapse multiple blank lines
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def latest_successful_by_qid_arm(answers: list[dict]) -> dict[tuple[str, str], dict]:
    """Dedupe: keep the LAST non-errored answer per (qid, arm)."""
    out: dict[tuple[str, str], dict] = {}
    for a in answers:
        if a.get("error") or not a.get("answer"):
            continue
        out[(a["question_id"], a["arm"])] = a
    return out


def call_judge(question: str, ans_a: str, ans_b: str,
               model: str = "haiku", timeout_s: int = 90) -> tuple[str, str, int, float, str | None]:
    """Returns (pick, reason, dur_ms, cost, error)."""
    user = f"""Question: {question}

Answer A:
{normalize(ans_a)}

Answer B:
{normalize(ans_b)}

Pick: """
    args = [
        "claude", "-p", "--output-format", "json", "--model", model,
        "--append-system-prompt", JUDGE_SYSTEM,
        "--disallowedTools",
        "Read Write Edit Bash WebSearch WebFetch Agent TaskCreate TaskUpdate TaskList",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=user, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return "TIE", "(timeout)", int((time.time()-t0)*1000), 0.0, "timeout"
    dur = int((time.time()-t0)*1000)
    if r.returncode != 0:
        return "TIE", "", dur, 0.0, f"exit {r.returncode}: {r.stderr[:200]}"
    try:
        j = json.loads(r.stdout)
    except Exception as e:
        return "TIE", "", dur, 0.0, f"json parse: {e}"
    if j.get("is_error"):
        return "TIE", "", dur, 0.0, f"claude is_error"
    text = j.get("result", "").strip()
    # First line is the pick; rest is reason
    first_line = text.split("\n", 1)[0].strip().upper()
    if first_line in ("A", "B", "TIE"):
        pick = first_line
        reason = text.split("\n", 1)[1].strip() if "\n" in text else ""
    elif first_line.startswith("A"):
        pick = "A"; reason = text
    elif first_line.startswith("B"):
        pick = "B"; reason = text
    else:
        pick = "TIE"; reason = text[:200]
    return pick, reason[:300], j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0), None


def load_done() -> set[tuple[str, str]]:
    if not JUDGMENTS.exists():
        return set()
    out = set()
    for ln in JUDGMENTS.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
            if not j.get("error"):
                out.add((j["question_id"], j["pair"]))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="A3_vs_A1,A3_vs_A0", help="comma-separated pairs to run")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--strata", default="")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=1, help="concurrent judge calls")
    args = ap.parse_args()

    pairs = args.pair.split(",")
    qs = {json.loads(ln)["id"]: json.loads(ln) for ln in QUESTIONS.read_text().splitlines() if ln.strip()}
    ans = [json.loads(ln) for ln in ANSWERS.read_text().splitlines() if ln.strip()]
    ans_by_key = latest_successful_by_qid_arm(ans)

    strata = set(args.strata.split(",")) if args.strata else None
    qids = sorted(qs.keys())
    if strata:
        qids = [qid for qid in qids if qs[qid]["stratum"] in strata]
    if args.limit:
        qids = qids[:args.limit]

    done = load_done()
    rng = random.Random(args.seed)
    total = len(qids) * len(pairs)
    t0 = time.time()

    # Build the task list (deterministic swap decided up front so seed is honored)
    tasks: list[tuple[str, str, str, str, dict, dict]] = []
    for qid in qids:
        for pair in pairs:
            if (qid, pair) in done:
                continue
            armA, armB = pair.split("_vs_")
            keyA = (qid, armA); keyB = (qid, armB)
            if keyA not in ans_by_key or keyB not in ans_by_key:
                with _WRITE_LOCK, JUDGMENTS.open("a") as fh:
                    fh.write(json.dumps({
                        "question_id": qid, "pair": pair,
                        "error": f"missing answer for {keyA if keyA not in ans_by_key else keyB}",
                    }) + "\n")
                continue
            swap = rng.random() < 0.5
            if swap:
                shown_a_arm, shown_b_arm = armB, armA
                shown_a, shown_b = ans_by_key[keyB], ans_by_key[keyA]
            else:
                shown_a_arm, shown_b_arm = armA, armB
                shown_a, shown_b = ans_by_key[keyA], ans_by_key[keyB]
            tasks.append((qid, pair, shown_a_arm, shown_b_arm, shown_a, shown_b))

    print(f"judging: {total} comparisons total · {len(done)} done · {len(tasks)} to do", file=sys.stderr)
    print(f"model={args.model} · workers={args.workers}", file=sys.stderr)

    counter = {"n": 0, "cost": 0.0}

    def work(task):
        qid, pair, sa_arm, sb_arm, sa, sb = task
        pick, reason, dur, cost, err = call_judge(
            qs[qid]["question"], sa["answer"], sb["answer"], model=args.model,
        )
        target = sa_arm if pick == "A" else (sb_arm if pick == "B" else "TIE")
        j = Judgment(qid, pair, sa_arm, sb_arm, pick, target, reason, dur, cost, err)
        with _WRITE_LOCK:
            with JUDGMENTS.open("a") as fh:
                fh.write(json.dumps(asdict(j), ensure_ascii=False) + "\n")
            counter["n"] += 1
            counter["cost"] += cost
            n = counter["n"]
            elapsed = time.time() - t0
            rate = n / max(elapsed, 1)
            eta_s = (len(tasks) - n) / max(rate, 0.001)
            status = ("✗ " + (err or "")[:40]) if err else f"→ {target}"
            print(
                f"[{n}/{len(tasks)}] {qid:>7} {pair:11} {dur/1000:5.1f}s  {status}  · eta {eta_s/60:.1f}min  · ${counter['cost']:.2f}",
                file=sys.stderr,
            )
        return j

    if args.workers <= 1:
        for t in tasks:
            work(t)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(work, t) for t in tasks]
            for _ in as_completed(futures):
                pass

    print(f"done. judgments in {JUDGMENTS}  · ${counter['cost']:.2f}  · {(time.time()-t0)/60:.1f}min", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
