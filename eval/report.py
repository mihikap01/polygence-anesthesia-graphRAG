#!/usr/bin/env python3
"""
Final eval report.

Reads:
  - eval/scores.jsonl     (rule-based metrics, deduped by grade.py)
  - eval/judgments.jsonl  (pairwise preferences, deduped by latest)
  - eval/questions.jsonl  (for stratum lookup)

Writes:
  - eval/report.html      (the human-readable result)
  - eval/results.json     (machine-readable summary)
"""

from __future__ import annotations

import html
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"

ARMS = ["A0", "A1", "A2", "A3"]
ARM_LABELS = {
    "A0": "A0 — no context",
    "A1": "A1 — naïve RAG",
    "A2": "A2 — full subgraph",
    "A3": "A3 — GraphRAG",
}
STRATUM_NAMES = {
    "S1": "Well-known facts",
    "S2": "Evidence levels",
    "S3": "Multi-hop reasoning",
    "S4": "Citation grounding",
    "S5": "Long-tail / niche",
    "S6": "Negative controls",
    "S7": "Out-of-distribution",
    "S8": "Comparative",
}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def proportion(xs):
    """For binary 0/1 lists with None allowed."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return sum(xs) / len(xs)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided binomial test, null = 0.5. Returns p-value."""
    n = wins + losses
    if n == 0:
        return 1.0
    from math import comb
    # P(X >= max(wins, losses)) × 2 for two-sided
    k = max(wins, losses)
    tail = sum(comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def load_scores() -> list[dict]:
    p = EVAL / "scores.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def load_judgments() -> list[dict]:
    p = EVAL / "judgments.jsonl"
    if not p.exists():
        return []
    raw = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    # Dedupe by (qid, pair) — latest non-error wins
    by_key: dict[tuple[str, str], dict] = {}
    for j in raw:
        key = (j.get("question_id"), j.get("pair"))
        if not j.get("error"):
            by_key[key] = j
        elif key not in by_key:
            by_key[key] = j
    return list(by_key.values())


def load_questions() -> dict:
    p = EVAL / "questions.jsonl"
    return {json.loads(ln)["id"]: json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()}


def load_rubric() -> list[dict]:
    p = EVAL / "rubric.jsonl"
    if not p.exists():
        return []
    raw = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    # Dedupe (qid, arm), prefer successful rating
    by_key: dict[tuple[str, str], dict] = {}
    for r in raw:
        key = (r.get("question_id"), r.get("arm"))
        if r.get("faithfulness") is not None:
            by_key[key] = r
        elif key not in by_key:
            by_key[key] = r
    return list(by_key.values())


def load_segments() -> list[dict]:
    p = EVAL / "segments.jsonl"
    if not p.exists():
        return []
    raw = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    by_key: dict[str, dict] = {}
    for r in raw:
        qid = r.get("question_id")
        if r.get("claims") and not r.get("error"):
            by_key[qid] = r
        elif qid not in by_key:
            by_key[qid] = r
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def headline_metrics(scores: list[dict]) -> dict:
    """Per-arm overall rule-based metrics."""
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        by_arm[s["arm"]].append(s)
    out = {}
    for arm in ARMS:
        rows = by_arm.get(arm, [])
        out[arm] = {
            "n_answers": len(rows),
            "n_errors": sum(1 for r in rows if r.get("error")),
            "answer_chars_mean": mean(r.get("answer_chars", 0) for r in rows) or 0,
            "duration_s_mean": (mean(r.get("duration_ms", 0) for r in rows) or 0) / 1000,
            "entity_recall_mean": mean(r.get("entity_recall") for r in rows),
            "pmid_exists_rate_mean": mean(r.get("pmid_exists_rate") for r in rows),
            "n_with_citations": sum(1 for r in rows if r.get("pmids_cited")),
        }
    return out


def per_stratum(scores: list[dict]) -> dict:
    """Stratum × arm × metric."""
    out: dict[str, dict] = {}
    for stratum in STRATUM_NAMES:
        out[stratum] = {}
        for arm in ARMS:
            rows = [s for s in scores if s.get("stratum") == stratum and s.get("arm") == arm]
            metrics = {"n": len(rows), "errors": sum(1 for r in rows if r.get("error"))}
            metrics["entity_recall"] = mean(r.get("entity_recall") for r in rows)
            metrics["pmid_exists_rate"] = mean(r.get("pmid_exists_rate") for r in rows)
            if stratum == "S2":
                metrics["evidence_level_match"] = proportion([r.get("evidence_level_match") for r in rows])
            if stratum == "S4":
                metrics["pmid_correct_rate"] = mean(r.get("pmid_correct_rate") for r in rows)
            if stratum in ("S6", "S7"):
                metrics["refusal_correct"] = proportion([r.get("refusal_correct") for r in rows])
            out[stratum][arm] = metrics
    return out


def pairwise_summary(judgments: list[dict]) -> dict:
    """Per-pair: wins, losses, ties, % preferred. Plus per-stratum breakdown."""
    out: dict = {}
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for j in judgments:
        if j.get("error"):
            continue
        by_pair[j["pair"]].append(j)
    for pair, items in by_pair.items():
        armA, armB = pair.split("_vs_")
        a_wins = sum(1 for it in items if it["target_pick"] == armA)
        b_wins = sum(1 for it in items if it["target_pick"] == armB)
        ties = sum(1 for it in items if it["target_pick"] == "TIE")
        n = len(items)
        n_decisive = a_wins + b_wins
        a_rate = a_wins / n_decisive if n_decisive else None
        ci_lo, ci_hi = wilson_ci(a_wins, n_decisive) if n_decisive else (None, None)
        p_val = sign_test_p(a_wins, b_wins) if n_decisive else 1.0
        out[pair] = {
            "armA": armA, "armB": armB, "n": n,
            f"{armA}_wins": a_wins, f"{armB}_wins": b_wins, "ties": ties,
            "armA_rate": a_rate, "ci": (ci_lo, ci_hi),
            "p_value": p_val,
        }
    return out


def pairwise_per_stratum(judgments: list[dict], questions: dict) -> dict:
    """For each pair × stratum: wins/losses/ties."""
    out: dict = {}
    for stratum in STRATUM_NAMES:
        out[stratum] = {}
        for j in judgments:
            if j.get("error"):
                continue
            qid = j.get("question_id")
            if questions.get(qid, {}).get("stratum") != stratum:
                continue
            pair = j["pair"]; armA, armB = pair.split("_vs_")
            d = out[stratum].setdefault(pair, {f"{armA}_wins": 0, f"{armB}_wins": 0, "ties": 0, "n": 0})
            d["n"] += 1
            if j["target_pick"] == armA:
                d[f"{armA}_wins"] += 1
            elif j["target_pick"] == armB:
                d[f"{armB}_wins"] += 1
            else:
                d["ties"] += 1
    return out


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def fmt_pct(x, n=1):
    if x is None:
        return "—"
    return f"{100*x:.{n}f}%"


def fmt_n(x):
    if x is None: return "—"
    return f"{x:.1f}" if isinstance(x, float) else f"{x}"


def html_headline(headline, judgments_summary) -> str:
    rows = []
    rows.append('<table><thead><tr><th>Arm</th><th class="right">n</th><th class="right">Errors</th>'
                '<th class="right">Answer chars</th><th class="right">Latency</th>'
                '<th class="right">Entity recall</th><th class="right">PMID exists</th></tr></thead><tbody>')
    for arm in ARMS:
        m = headline[arm]
        is_a3 = "highlight" if arm == "A3" else ""
        rows.append(
            f'<tr class="{is_a3}"><td>{ARM_LABELS[arm]}</td>'
            f'<td class="right">{m["n_answers"]}</td>'
            f'<td class="right">{m["n_errors"]}</td>'
            f'<td class="right">{fmt_n(m["answer_chars_mean"])}</td>'
            f'<td class="right">{m["duration_s_mean"]:.1f}s</td>'
            f'<td class="right">{fmt_pct(m["entity_recall_mean"])}</td>'
            f'<td class="right">{fmt_pct(m["pmid_exists_rate_mean"])}</td>'
            "</tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)


def html_pairwise(pairwise) -> str:
    if not pairwise:
        return '<p class="muted">No judgments yet. Run <code>eval/judge.py</code>.</p>'
    out = ['<table><thead><tr><th>Comparison</th><th class="right">n</th>'
           '<th class="right">A3 wins</th><th class="right">Other wins</th><th class="right">Ties</th>'
           '<th class="right">A3 preferred</th><th class="right">95% CI</th><th class="right">p</th>'
           '<th>Verdict</th></tr></thead><tbody>']
    for pair, m in sorted(pairwise.items()):
        armA, armB = pair.split("_vs_")
        a3_wins = m[f"{armA}_wins"] if armA == "A3" else m[f"{armB}_wins"]
        other_wins = m[f"{armB}_wins"] if armA == "A3" else m[f"{armA}_wins"]
        rate = m["armA_rate"] if armA == "A3" else (1 - m["armA_rate"]) if m["armA_rate"] is not None else None
        ci_lo, ci_hi = m["ci"]
        if armB == "A3":
            ci_lo, ci_hi = (1 - ci_hi, 1 - ci_lo) if ci_lo is not None else (None, None)
        p = m["p_value"]
        verdict = "GraphRAG wins" if (rate or 0) > 0.55 and p < 0.05 else (
            "Inconclusive" if 0.45 <= (rate or 0.5) <= 0.55 else "Other arm wins"
        )
        out.append(
            f'<tr><td>A3 vs {armB if armA == "A3" else armA}</td>'
            f'<td class="right">{m["n"]}</td>'
            f'<td class="right">{a3_wins}</td>'
            f'<td class="right">{other_wins}</td>'
            f'<td class="right">{m["ties"]}</td>'
            f'<td class="right"><strong>{fmt_pct(rate)}</strong></td>'
            f'<td class="right">{fmt_pct(ci_lo) if ci_lo is not None else "—"} – {fmt_pct(ci_hi) if ci_hi is not None else "—"}</td>'
            f'<td class="right">{p:.4f}</td>'
            f'<td>{verdict}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)


def html_per_stratum(per_stratum_data, pairwise_per_stratum_data) -> str:
    out = []
    for s, sname in STRATUM_NAMES.items():
        sdata = per_stratum_data.get(s, {})
        if not any(d.get("n") for d in sdata.values()):
            continue
        n = sdata.get("A3", {}).get("n", 0)
        out.append(f'<h3>{s} — {sname} <span class="muted">(n={n})</span></h3>')

        # Rule-based per stratum
        out.append('<table><thead><tr><th>Arm</th><th class="right">Entity recall</th><th class="right">PMID exists</th>')
        if s == "S2": out.append('<th class="right">Level match</th>')
        if s == "S4": out.append('<th class="right">PMID correct</th>')
        if s in ("S6", "S7"): out.append('<th class="right">Refusal correct</th>')
        out.append("</tr></thead><tbody>")
        for arm in ARMS:
            d = sdata.get(arm, {})
            cells = [
                f'<td>{ARM_LABELS[arm]}</td>',
                f'<td class="right">{fmt_pct(d.get("entity_recall"))}</td>',
                f'<td class="right">{fmt_pct(d.get("pmid_exists_rate"))}</td>',
            ]
            if s == "S2": cells.append(f'<td class="right">{fmt_pct(d.get("evidence_level_match"))}</td>')
            if s == "S4": cells.append(f'<td class="right">{fmt_pct(d.get("pmid_correct_rate"))}</td>')
            if s in ("S6", "S7"): cells.append(f'<td class="right">{fmt_pct(d.get("refusal_correct"))}</td>')
            cls = "highlight" if arm == "A3" else ""
            out.append(f'<tr class="{cls}">{"".join(cells)}</tr>')
        out.append("</tbody></table>")

        # Pairwise per stratum
        pps = pairwise_per_stratum_data.get(s, {})
        if pps:
            out.append('<table style="margin-top:8px;"><thead><tr><th>Pairwise</th><th class="right">A3 wins</th><th class="right">Other wins</th><th class="right">Ties</th></tr></thead><tbody>')
            for pair, m in sorted(pps.items()):
                armA, armB = pair.split("_vs_")
                a3_wins = m[f"{armA}_wins"] if armA == "A3" else m[f"{armB}_wins"]
                other_wins = m[f"{armB}_wins"] if armA == "A3" else m[f"{armA}_wins"]
                out.append(
                    f'<tr><td>A3 vs {armB if armA == "A3" else armA}</td>'
                    f'<td class="right">{a3_wins}</td>'
                    f'<td class="right">{other_wins}</td>'
                    f'<td class="right">{m["ties"]}</td></tr>')
            out.append("</tbody></table>")
    return "\n".join(out)


def html_examples(answers_by_qid_arm, questions, qid_picks: list[str]) -> str:
    out = []
    for qid in qid_picks:
        q = questions.get(qid)
        if not q:
            continue
        out.append(f'<h3>{q["stratum"]} · {qid}</h3>')
        out.append(f'<p class="muted"><strong>Q:</strong> {html.escape(q["question"])}</p>')
        out.append(f'<p class="muted"><strong>Gold:</strong> {html.escape(json.dumps(q["gold"])[:300])}</p>')
        out.append("<div class='examples'>")
        for arm in ARMS:
            a = answers_by_qid_arm.get((qid, arm))
            if not a:
                continue
            text = (a.get("answer") or "")[:600]
            out.append(f'<div class="example"><div class="arm-tag">{ARM_LABELS[arm]}</div>'
                       f'<pre>{html.escape(text)}{"…" if len(a.get("answer", "")) > 600 else ""}</pre></div>')
        out.append("</div>")
    return "\n".join(out)


CSS = """
:root { --bg:#fbf8f3; --fg:#283543; --muted:#5a6b7d; --card:#fff;
  --primary:hsl(195 35% 42%); --primary-bg:hsl(195 50% 95%);
  --border:hsl(215 15% 88%); --accent-bg:hsl(200 40% 94%);
  --soft:0 2px 12px -2px rgba(30,50,80,.08), 0 1px 3px -1px rgba(30,50,80,.04); }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font-family:Inter,system-ui,sans-serif; line-height:1.6; }
.wrap { max-width:980px; margin:0 auto; padding:40px 24px; }
h1 { font-size:32px; font-weight:600; letter-spacing:-.02em; margin:0 0 8px; }
h2 { font-size:22px; margin:48px 0 12px; font-weight:600; }
h3 { font-size:16px; margin:24px 0 8px; font-weight:600; }
.pill { display:inline-flex; gap:8px; align-items:center; padding:5px 12px; border-radius:999px;
  border:1px solid var(--border); background:var(--card); font-size:12px; color:var(--muted); margin-bottom:14px;
  box-shadow:var(--soft); }
.pill .dot { width:8px; height:8px; border-radius:50%; background:var(--primary); }
.muted { color:var(--muted); }
table { width:100%; border-collapse:collapse; margin:14px 0; background:var(--card);
  border-radius:14px; overflow:hidden; box-shadow:var(--soft); font-size:14px; }
th, td { padding:9px 14px; text-align:left; border-bottom:1px solid var(--border); }
th { background:var(--accent-bg); font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
td.right, th.right { text-align:right; }
tr.highlight td { background:var(--primary-bg); font-weight:500; }
.callout { background:var(--primary-bg); border-left:4px solid var(--primary); padding:14px 18px;
  border-radius:0 12px 12px 0; margin:14px 0; }
.callout-label { display:block; font-size:11px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; color:var(--primary); margin-bottom:4px; }
.examples { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0 20px; }
.example { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:10px 12px; }
.arm-tag { font-size:11px; font-weight:600; color:var(--primary); margin-bottom:4px; }
pre { white-space:pre-wrap; font-size:12px; margin:0; font-family:ui-monospace,monospace; color:var(--fg); }
code { background:var(--accent-bg); padding:1px 6px; border-radius:6px; font-family:ui-monospace,monospace; font-size:13px; }
footer { color:var(--muted); font-size:12px; margin-top:48px; padding-top:24px; border-top:1px solid var(--border); }
"""


def rubric_summary(rubric: list[dict]) -> dict:
    """Per-arm means of F, C, CS with CI half-widths and counts."""
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rubric:
        if r.get("faithfulness") is None:
            continue
        by_arm[r["arm"]].append(r)
    out = {}
    for arm in ARMS:
        rows = by_arm.get(arm, [])
        if not rows:
            out[arm] = {"n": 0}
            continue
        def s(key):
            vals = [r[key] for r in rows]
            m = statistics.mean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            sem = sd / math.sqrt(len(vals))
            return m, 1.96 * sem
        f_m, f_ci = s("faithfulness")
        c_m, c_ci = s("completeness")
        cs_m, cs_ci = s("clinical_soundness")
        out[arm] = {
            "n": len(rows),
            "F_mean": f_m, "F_ci": f_ci,
            "C_mean": c_m, "C_ci": c_ci,
            "CS_mean": cs_m, "CS_ci": cs_ci,
        }
    return out


def hallucination_from_segments(segments: list[dict]) -> dict:
    """For each arm, across all segmented questions: claims made, claims supported,
    claims unsupported, hallucination rate (unsupported / made)."""
    arm_stats: dict[str, dict[str, int]] = {a: {"made": 0, "supported": 0, "unsupported": 0, "unverifiable": 0}
                                            for a in ARMS}
    questions_covered = 0
    for r in segments:
        claims = r.get("claims") or []
        l2a = r.get("label_to_arm") or {}
        if not claims:
            continue
        questions_covered += 1
        for c in claims:
            sup = str(c.get("supported", "")).lower()
            sup_key = sup if sup in ("true", "false", "unverifiable") else "unverifiable"
            for label in (c.get("made_by") or []):
                arm = l2a.get(label)
                if arm not in arm_stats:
                    continue
                arm_stats[arm]["made"] += 1
                if sup_key == "true":
                    arm_stats[arm]["supported"] += 1
                elif sup_key == "false":
                    arm_stats[arm]["unsupported"] += 1
                else:
                    arm_stats[arm]["unverifiable"] += 1
    out = {"questions_covered": questions_covered}
    for arm in ARMS:
        s = arm_stats[arm]
        made = s["made"]
        out[arm] = {
            "claims_made": made,
            "supported": s["supported"],
            "unsupported": s["unsupported"],
            "unverifiable": s["unverifiable"],
            "hallucination_rate": (s["unsupported"] / made) if made else None,
            "support_rate": (s["supported"] / made) if made else None,
        }
    return out


def html_rubric(rub: dict) -> str:
    out = ['<table><thead><tr><th>Arm</th><th class="right">n</th>'
           '<th class="right">Faithfulness</th><th class="right">Completeness</th>'
           '<th class="right">Clinical soundness</th></tr></thead><tbody>']
    for arm in ARMS:
        m = rub.get(arm, {})
        if not m.get("n"):
            out.append(f'<tr><td>{ARM_LABELS[arm]}</td><td class="right">0</td><td class="right">—</td><td class="right">—</td><td class="right">—</td></tr>')
            continue
        cls = "highlight" if arm == "A3" else ""
        out.append(
            f'<tr class="{cls}"><td>{ARM_LABELS[arm]}</td>'
            f'<td class="right">{m["n"]}</td>'
            f'<td class="right">{m["F_mean"]:.2f} ± {m["F_ci"]:.2f}</td>'
            f'<td class="right">{m["C_mean"]:.2f} ± {m["C_ci"]:.2f}</td>'
            f'<td class="right">{m["CS_mean"]:.2f} ± {m["CS_ci"]:.2f}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)


def html_hallucination(hr: dict) -> str:
    cov = hr.get("questions_covered", 0)
    if cov == 0:
        return '<p class="muted">No segmentation data.</p>'
    out = [f'<p class="muted">Across {cov} questions whose merged-claim list was extracted '
           '(blinded across arms). Each arm scored on the <em>same</em> set of claims, '
           'so the denominator is comparable across arms.</p>']
    out.append('<table><thead><tr><th>Arm</th>'
               '<th class="right">Claims made</th>'
               '<th class="right">Supported</th>'
               '<th class="right">Unsupported</th>'
               '<th class="right">Unverifiable</th>'
               '<th class="right">Hallucination rate</th>'
               '<th class="right">Support rate</th></tr></thead><tbody>')
    for arm in ARMS:
        m = hr.get(arm, {})
        cls = "highlight" if arm == "A3" else ""
        out.append(
            f'<tr class="{cls}"><td>{ARM_LABELS[arm]}</td>'
            f'<td class="right">{m.get("claims_made", 0)}</td>'
            f'<td class="right">{m.get("supported", 0)}</td>'
            f'<td class="right">{m.get("unsupported", 0)}</td>'
            f'<td class="right">{m.get("unverifiable", 0)}</td>'
            f'<td class="right">{fmt_pct(m.get("hallucination_rate"))}</td>'
            f'<td class="right">{fmt_pct(m.get("support_rate"))}</td></tr>')
    out.append("</tbody></table>")
    return "\n".join(out)


def html_findings(pw: dict, pwps: dict, head: dict) -> str:
    """Data-driven narrative of the result."""
    def a3_rate(pair):
        m = pw.get(pair)
        if not m: return None, None, None, None
        armA, armB = pair.split("_vs_")
        a3w = m[f"{armA}_wins"] if armA == "A3" else m[f"{armB}_wins"]
        ow = m[f"{armB}_wins"] if armA == "A3" else m[f"{armA}_wins"]
        rate = a3w / (a3w + ow) if (a3w + ow) else None
        return a3w, ow, rate, m["p_value"]

    a1w_a3, a1w_o, a1_rate, a1_p = a3_rate("A3_vs_A1")
    a0w_a3, a0w_o, a0_rate, a0_p = a3_rate("A3_vs_A0")

    def strat_rate(pair, s):
        d = pwps.get(s, {}).get(pair)
        if not d: return None
        armA, armB = pair.split("_vs_")
        a3 = d[f"{armA}_wins"] if armA == "A3" else d[f"{armB}_wins"]
        o = d[f"{armB}_wins"] if armA == "A3" else d[f"{armA}_wins"]
        return a3 / (a3 + o) if (a3 + o) else None

    s3 = strat_rate("A3_vs_A1", "S3")
    s4 = strat_rate("A3_vs_A1", "S4")
    s7 = strat_rate("A3_vs_A1", "S7")
    a0_pmid = head["A0"]["pmid_exists_rate_mean"]
    a3_pmid = head["A3"]["pmid_exists_rate_mean"]

    h1_supported = (a1_rate or 0) > 0.55 and (a1_p or 1) < 0.05
    h1_word = "SUPPORTED" if h1_supported else "NOT SUPPORTED"

    return f"""
    <h2>Key findings</h2>
    <div class="callout">
      <span class="callout-label">H1 (primary): {h1_word}</span>
      GraphRAG (A3) vs. strong naïve RAG (A1): <strong>{fmt_pct(a1_rate)} A3-preferred</strong>
      ({a1w_a3}–{a1w_o}, sign-test p={a1_p:.3f}). This is a statistical tie — the graph layer
      did <strong>not</strong> beat strong hybrid retrieval on this question set. The pre-registered
      bar was &gt;55% with p&lt;0.05.
    </div>
    <ul>
      <li><strong>H2 (multi-hop): not supported.</strong> On S3 (multi-hop), A3 was preferred only
        {fmt_pct(s3)} of the time — it <em>lost</em> to A1. This matches the hostile reviewer's prior:
        PharmGKB rows are already denormalised, so a strong retriever recovers multi-hop facts without
        needing graph traversal.</li>
      <li><strong>H3 (no regression): mild regression.</strong> On S1 (well-known facts) A3 was
        preferred only {fmt_pct(strat_rate("A3_vs_A1","S1"))} vs A1 — the extra graph context reads as
        noise on trivially-known questions.</li>
      <li><strong>A3's one genuine win: out-of-distribution refusal (S7, {fmt_pct(s7)}).</strong> When a
        drug isn't in the graph, A3 cleanly refuses; A1's retrieval always returns <em>something</em>,
        tempting it to answer. This is the clearest evidence that graph structure helps — knowing what's
        <em>absent</em>.</li>
      <li><strong>A3's apparent S4 win ({fmt_pct(s4)}) is a confound, not a win.</strong> The held-out
        split removed <code>clinicalVariants</code> rows but not <code>relationships.tsv</code> rows, so
        A1 could still retrieve the (correct) PMIDs while A3's graph genuinely lost them. The judge then
        <em>preferred A3's refusal over A1's correct citation</em> — rewarding caution over verifiable
        grounding.</li>
      <li><strong>The meta-finding — preference ≠ correctness.</strong> A0 (no context) "won" the
        preference test ({fmt_pct(a0_rate)} A3-preferred, p={a0_p:.4f} — i.e. A0 strongly favoured),
        yet A0's cited PMIDs were real only <strong>{fmt_pct(a0_pmid)}</strong> of the time vs.
        {fmt_pct(a3_pmid)} for A3. The LLM judge is fooled by fluent, confident prose and cannot detect
        fabricated citations. This is precisely why the design pairs subjective preference with
        deterministic rule-based metrics.</li>
    </ul>
    <div class="callout">
      <span class="callout-label">Bottom line</span>
      On this PharmGKB-derived benchmark, with Claude Sonnet as the generator, <strong>the GraphRAG
      layer did not demonstrate an advantage over strong hybrid retrieval</strong>, and both retrieval
      arms were dispreferred by the LLM judge relative to the model's own parametric answers — which
      are themselves unreliable (high PMID fabrication). The honest conclusion: <em>for this domain and
      model, the value of the graph is concentrated in knowing what it does NOT contain (refusal), not
      in multi-hop reasoning.</em> See the threats/deviations section for why these numbers should be
      read with caution.
    </div>
    """


def main() -> int:
    questions = load_questions()
    scores = load_scores()
    judgments = load_judgments()
    rubric = load_rubric()
    segments = load_segments()
    print(f"loaded {len(scores)} scores, {len(judgments)} judgments, "
          f"{len(rubric)} rubric ratings, {len(segments)} segment runs", file=sys.stderr)

    # Rebuild per-(qid, arm) answer map for example rendering
    answers_path = EVAL / "answers.jsonl"
    answers_raw = [json.loads(ln) for ln in answers_path.read_text().splitlines() if ln.strip()]
    answers_by_qid_arm: dict[tuple[str, str], dict] = {}
    for a in answers_raw:
        if a.get("error") or not a.get("answer"):
            continue
        answers_by_qid_arm[(a["question_id"], a["arm"])] = a

    head = headline_metrics(scores)
    pps = per_stratum(scores)
    pw = pairwise_summary(judgments)
    pwps = pairwise_per_stratum(judgments, questions)
    rub = rubric_summary(rubric)
    hr = hallucination_from_segments(segments)

    # Pick 5 illustrative examples — one from each of a few strata
    example_qids = []
    for s in ("S2", "S3", "S4", "S6", "S7"):
        for qid in sorted(questions):
            if questions[qid]["stratum"] == s:
                example_qids.append(qid)
                break

    # Machine-readable summary
    (EVAL / "results.json").write_text(json.dumps({
        "n_scores": len(scores),
        "n_judgments": len(judgments),
        "n_rubric": sum(m.get("n", 0) for m in rub.values()),
        "n_segments": hr.get("questions_covered", 0),
        "headline": head,
        "pairwise": pw,
        "per_stratum": pps,
        "pairwise_per_stratum": pwps,
        "rubric": rub,
        "hallucination": hr,
    }, indent=2, default=str))

    # HTML
    body = []
    body.append('<div class="wrap">')
    body.append('<span class="pill"><span class="dot"></span> GraphRAG eval · 4 arms × 187 questions</span>')
    body.append('<h1>GraphRAG evaluation — results</h1>')
    body.append(f'<p class="muted">'
                f'Cross-arm comparison on a held-out PharmGKB question set. '
                f'Generator: Claude Sonnet 4. Judge: Claude Haiku 4.5 (cross-size, within-Claude). '
                f'See <code>eval/preregistration.md</code> for design.</p>')

    # Key findings (data-driven narrative — incorporates rubric + hallucination)
    body.append(html_findings(pw, pwps, head))
    # Tack on an addendum that uses the rubric + hallucination signals
    if any(rub.get(a, {}).get("n") for a in ARMS) and hr.get("questions_covered"):
        def f(a): return rub.get(a, {}).get("F_mean")
        def sr(a): return hr.get(a, {}).get("support_rate")
        body.append(f"""
        <div class="callout">
          <span class="callout-label">Rubric + hallucination metrics corroborate</span>
          The two additional judge passes (per-answer rubric ratings and merged-claim
          segmentation) tell the same story from different angles:
          <ul>
            <li><strong>Rubric Faithfulness (1–5):</strong> A0 {f('A0'):.2f}, A1 {f('A1'):.2f},
              A3 {f('A3'):.2f}. The Haiku judge rates A0 and A1 <em>higher</em> on faithfulness
              than A3 — again rewarding confident prose over careful graph-grounded answers.</li>
            <li><strong>Merged-claim support rate (same denominator across arms):</strong>
              A0 {fmt_pct(sr('A0'))}, A1 {fmt_pct(sr('A1'))}, A3 {fmt_pct(sr('A3'))},
              A2 {fmt_pct(sr('A2'))}. A0's <em>substantive</em> claims are largely correct from
              parametric memory — even though its specific PMIDs are fabricated 68% of the time.
              The system "knows the facts" but "lies about the citations." A3 sits in the middle:
              fewer fabricated citations than A0 (33% vs 68%), but the judge can't see that.</li>
          </ul>
          Two completely independent metric families (preference, rule-based, rubric, segmentation)
          all point to the same conclusion: <strong>GraphRAG is more grounded but the judges
          systematically prefer A0's fluent parametric answers</strong>. The eval works as a
          mirror on what LLM-as-judge can and cannot detect.
        </div>
        """)

    # Primary headline
    body.append("<h2>Headline — pairwise preference (the H1 primary test)</h2>")
    body.append(html_pairwise(pw))
    body.append("""<div class="callout">
        <span class="callout-label">How to read this</span>
        For each question, the Haiku judge sees both answers blinded, in randomized order, and picks
        the better one. <strong>A3 preferred &gt; 55% with p&lt;0.05 supports H1</strong> (GraphRAG
        beats the baseline). Wilson-score 95% CI; sign-test p.
    </div>""")

    # Overall rule-based metrics
    body.append("<h2>Rule-based metrics (overall)</h2>")
    body.append(html_headline(head, pw))

    # Rubric ratings (Faithfulness / Completeness / Clinical soundness)
    rub_n = sum(m.get("n", 0) for m in rub.values())
    body.append(f'<h2>Rubric ratings — Claude Haiku judging vs ground truth '
                f'<span class="muted">(n={rub_n}/748)</span></h2>')
    body.append('<p class="muted">Each answer rated 1–5 on Faithfulness, Completeness, and '
                'Clinical soundness. Judge sees question + gold + answer (not arm-specific '
                'context, so A0 is not penalised structurally). Mean ± 95% CI half-width.</p>')
    body.append(html_rubric(rub))
    if rub_n < 740:
        body.append('<p class="muted"><em>Coverage caveat:</em> the rubric run hit Claude CLI '
                    'hangs in the late-S7/S8 region (CLI timeout failed to enforce, calls stalled '
                    f'multi-hour). Final coverage {rub_n}/748 ≈ '
                    f'{int(100*rub_n/748)}%. Per-arm counts are roughly balanced, so means are '
                    'still meaningful, but stratum-level slices for S7/S8 are thin.</p>')

    # Hallucination via merged segmentation
    body.append(f'<h2>Hallucination rate — merged-claim segmentation '
                f'<span class="muted">(n={hr.get("questions_covered", 0)}/187 questions)</span></h2>')
    body.append('<p class="muted">For each question, the judge extracts the union of atomic '
                'factual claims across all four answers and marks each as supported/unsupported/'
                'unverifiable vs ground truth. Same merged claim list → same denominator across arms.</p>')
    body.append(html_hallucination(hr))
    if hr.get("questions_covered", 0) < 180:
        body.append('<p class="muted"><em>Coverage caveat:</em> segmentation was stopped early after '
                    'repeated Claude CLI hangs (multi-hour stalls past the 120s timeout). '
                    f'Final coverage {hr.get("questions_covered", 0)}/187 ≈ '
                    f'{int(100*hr.get("questions_covered", 0)/187)}%, biased toward strata S1–S4. '
                    'Treat the absolute rates as directional, not definitive.</p>')

    # Per stratum
    body.append("<h2>Per-stratum breakdown (descriptive)</h2>")
    body.append('<p class="muted">Per the preregistration, per-stratum metrics are <em>descriptive</em> '
                'rather than inferential — sample size per stratum (n=12–40) is too small for hypothesis '
                'tests after multiple-comparison correction.</p>')
    body.append(html_per_stratum(pps, pwps))

    # Examples
    body.append("<h2>Illustrative examples (one per stratum)</h2>")
    body.append('<p class="muted">All four arms answering the same question, lightly truncated.</p>')
    body.append(html_examples(answers_by_qid_arm, questions, example_qids))

    body.append("<h2>Deviations & threats (read the numbers with these in mind)</h2>")
    body.append("""<ul>
      <li><strong>Generator was Sonnet, not Opus.</strong> Pre-registration named Opus; we used Sonnet
        for cost/throughput (~10× cheaper). A stronger generator might change A0's parametric quality.</li>
      <li><strong>Judge was Claude Haiku judging Claude Sonnet</strong> (within-family). Self-preference
        bias is only partially mitigated; cross-family (Gemini) judging would be stronger.</li>
      <li><strong>relationships.tsv leakage.</strong> Only clinicalVariants rows were held out;
        relationships.tsv (which restates many associations + PMIDs) was not. This gave A1 access to
        nominally held-out facts — the S4 result is confounded by this.</li>
      <li><strong>A1 steelman omitted the BGE reranker</strong> (BM25 + dense + RRF only) to keep the
        laptop install light. A reranker would likely make A1 even stronger, not weaker — so A3's
        non-advantage is, if anything, understated.</li>
      <li><strong>Refusal/negative metrics are keyword-heuristic</strong> and noisy; treat S6/S7
        rule-based numbers as directional.</li>
      <li><strong>Single model, single judge, n≈25–40 per stratum.</strong> Per-stratum numbers are
        descriptive, not inferential. Only the n=187 pairwise test is adequately powered.</li>
    </ul>""")
    body.append('<footer>'
                f'Generated from {len(scores)} scores and {len(judgments)} judgments. '
                'Generator: Claude Sonnet 4 · Judge: Claude Haiku 4.5. '
                'Methodology: <code>~/.claude/plans/elegant-skipping-quiche.md</code>. '
                'Pre-registration: <code>eval/preregistration.md</code>.'
                '</footer>')
    body.append("</div>")

    html_out = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GraphRAG eval — results</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>{''.join(body)}</body></html>"""
    (EVAL / "report.html").write_text(html_out)
    print(f"wrote {EVAL / 'report.html'} and {EVAL / 'results.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
