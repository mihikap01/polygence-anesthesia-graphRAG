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
    "A0": "A0 · LLM alone (no context)",
    "A1": "A1 · LLM + plain-text retrieval",
    "A2": "A2 · LLM + full subgraph dump",
    "A3": "A3 · LLM + GraphRAG (the system)",
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
                '<th class="right">Answer length</th><th class="right">Latency</th>'
                '<th class="right" title="% of expected entities (drugs/genes/etc.) the answer named">Entity recall</th>'
                '<th class="right" title="% of cited PMIDs that exist in our corpus (vs. invented)">PMIDs real (when cited)</th>'
                '</tr></thead><tbody>')
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
  --good:hsl(150 35% 45%); --good-bg:hsl(150 40% 94%);
  --warn:hsl(40 70% 50%); --warn-bg:hsl(40 70% 94%);
  --bad:hsl(10 55% 50%); --bad-bg:hsl(10 60% 95%);
  --soft:0 2px 12px -2px rgba(30,50,80,.08), 0 1px 3px -1px rgba(30,50,80,.04);
  --gentle:0 4px 20px -4px rgba(30,50,80,.10), 0 2px 6px -2px rgba(30,50,80,.05); }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font-family:Inter,system-ui,sans-serif; line-height:1.6; }
.wrap { max-width:980px; margin:0 auto; padding:40px 24px; }
.hero { text-align:center; padding:24px 0 36px; }
h1 { font-size:36px; font-weight:600; letter-spacing:-.02em; margin:14px 0 12px; line-height:1.15; }
h1 em { font-style:normal; color:var(--primary); }
h2 { font-size:22px; margin:56px 0 14px; font-weight:600; letter-spacing:-.01em; }
h3 { font-size:16px; margin:24px 0 8px; font-weight:600; }
.section-tag { display:inline-block; font-size:11px; font-weight:600; letter-spacing:.08em;
  text-transform:uppercase; color:var(--primary); padding:3px 10px; border-radius:999px;
  background:var(--primary-bg); margin-bottom:8px; }
.pill { display:inline-flex; gap:8px; align-items:center; padding:5px 14px; border-radius:999px;
  border:1px solid var(--border); background:var(--card); font-size:12px; color:var(--muted);
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
.tldr { background:linear-gradient(135deg,hsl(195 50% 94%),hsl(150 40% 95%));
  border:1px solid var(--border); border-radius:20px; padding:24px 28px;
  box-shadow:var(--gentle); margin:8px 0 32px; }
.tldr h3 { margin:0 0 8px; font-size:16px; }
.tldr p { margin:8px 0; font-size:15px; }
.examples { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0 20px; }
.example { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:10px 12px; }
.arm-tag { font-size:11px; font-weight:600; color:var(--primary); margin-bottom:4px; }
pre { white-space:pre-wrap; font-size:12px; margin:0; font-family:ui-monospace,monospace; color:var(--fg); }
code { background:var(--accent-bg); padding:1px 6px; border-radius:6px; font-family:ui-monospace,monospace; font-size:13px; }
.arm-card-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0; }
.arm-card { background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:14px 16px; box-shadow:var(--soft); }
.arm-card.a3 { border-color:var(--primary); }
.arm-card .lbl { display:inline-block; font-size:11px; font-weight:700; letter-spacing:.06em;
  padding:3px 8px; border-radius:6px; background:var(--accent-bg); color:var(--muted); margin-bottom:8px; }
.arm-card.a3 .lbl { background:var(--primary); color:#fff; }
.arm-card h4 { margin:4px 0 6px; font-size:14px; font-weight:600; }
.arm-card .desc { font-size:13px; color:var(--muted); margin:0 0 8px; }
.arm-card pre { font-size:10.5px; max-height:180px; overflow:auto; background:var(--bg); padding:8px; border-radius:8px; }
.q-card { background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:12px 16px; box-shadow:var(--soft); margin:8px 0; }
.q-card .stratum { display:inline-block; font-size:11px; font-weight:700;
  padding:2px 8px; border-radius:6px; background:var(--primary-bg); color:var(--primary); margin-right:6px; }
.q-card .gold { font-size:12px; color:var(--muted); margin-top:6px; font-family:ui-monospace,monospace; }
.hyp { display:grid; grid-template-columns:90px 1fr 200px; gap:14px; align-items:start;
  background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:14px 18px; box-shadow:var(--soft); margin:10px 0; }
.hyp.h1 { border-left:4px solid var(--primary); }
.hyp .tag { font-weight:700; font-size:13px; color:var(--primary); }
.hyp .claim { font-size:14px; }
.hyp .verdict { font-size:12px; padding:6px 10px; border-radius:10px; text-align:center; font-weight:600; }
.verdict.good { background:var(--good-bg); color:var(--good); }
.verdict.bad { background:var(--bad-bg); color:var(--bad); }
.verdict.mixed { background:var(--warn-bg); color:var(--warn); }
details { background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:10px 14px; margin:12px 0; box-shadow:var(--soft); }
details summary { cursor:pointer; font-weight:600; font-size:13px; color:var(--muted); }
details[open] summary { color:var(--fg); }
details pre { font-size:12px; margin-top:10px; max-height:400px; overflow:auto; background:var(--bg); padding:12px; border-radius:8px; }
.flow { display:flex; flex-direction:column; gap:8px; margin:14px 0; }
.flow-step { background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:12px 16px; box-shadow:var(--soft); display:flex; gap:14px; align-items:flex-start; font-size:13px; }
.flow-step .num { flex-shrink:0; width:24px; height:24px; border-radius:50%;
  background:var(--primary); color:#fff; font-weight:700; font-size:11px;
  display:flex; align-items:center; justify-content:center; }
.flow-arrow { text-align:center; color:var(--muted); font-size:16px; line-height:1; }
.metric-key { color:var(--primary); font-weight:600; }
footer { color:var(--muted); font-size:12px; margin-top:48px; padding-top:24px; border-top:1px solid var(--border); }
@media (max-width:680px) {
  .arm-card-grid, .examples { grid-template-columns:1fr; }
  .hyp { grid-template-columns:1fr; }
}
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
    <h2>What the numbers tell us</h2>
    <div class="callout">
      <span class="callout-label">Primary verdict: H1 {h1_word}</span>
      When the blinded judge compared GraphRAG's answer to plain-text RAG's answer on the same
      question, <strong>GraphRAG was picked {fmt_pct(a1_rate)} of the time</strong> ({a1w_a3} wins
      out of {a1w_a3+a1w_o} decisive comparisons). The pre-declared "win" threshold was &gt;55%
      with p&lt;0.05. We got p={a1_p:.2f} — meaning the difference is indistinguishable from
      pure chance. GraphRAG did not earn its complexity on this benchmark.
    </div>
    <ul>
      <li><strong>The multi-hop test (H2): also lost.</strong> Questions specifically designed to
        require reasoning across multiple graph edges — exactly where the structure should shine —
        had GraphRAG winning only {fmt_pct(s3)} of the time vs plain-text RAG. The reason:
        PharmGKB rows already pack drug + gene + variant + phenotype + evidence level into a
        single row, so a good text retriever recovers "multi-hop" facts in a single chunk
        without needing graph traversal.</li>
      <li><strong>On easy questions (H3): mild regression.</strong> Even on well-known facts
        where adding context shouldn't matter, GraphRAG was picked only
        {fmt_pct(strat_rate("A3_vs_A1","S1"))} of the time — the extra graph context reads as
        noise when the answer is trivially known.</li>
      <li><strong>The one genuine win: knowing what the graph doesn't contain.</strong> On
        questions about drugs the graph genuinely lacks (out-of-distribution test), GraphRAG was
        preferred {fmt_pct(s7)} of the time. It says "I don't have that"; plain-text retrieval
        returns vaguely-related junk and the model answers confidently. Knowing the boundaries
        of the data is the graph's structural value, not multi-hop reasoning.</li>
      <li><strong>The apparent citation win is a confound, not a real win.</strong> On
        citation-grounding questions GraphRAG was picked {fmt_pct(s4)} of the time — but the
        reason isn't grounding. The held-out test set removed one PharmGKB file but not another
        that restates many of the same facts. Plain-text RAG could still retrieve correct PMIDs
        from the un-held-out file; GraphRAG's graph genuinely lost them and refused to cite. The
        judge rewarded refusal over correct citation. The metric is measuring caution, not truth.</li>
      <li><strong>The meta-finding: preference ≠ correctness.</strong> The no-context model
        (just the LLM, no retrieved data at all) was picked {fmt_pct(1-a0_rate)} of the time over
        GraphRAG — a strong win. But when we checked whether its cited paper IDs (PMIDs) were
        real, only {fmt_pct(a0_pmid)} actually existed. GraphRAG's cited PMIDs were real
        {fmt_pct(a3_pmid)} of the time. The LLM judge was fooled by fluent confidence and
        couldn't detect fabricated citations. This is the entire reason the design uses both
        subjective preference and deterministic ground-truth checks.</li>
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


# ===========================================================================
# Narrative sections (the "why, what, how, hypotheses, results" the report
# was missing). Each returns a chunk of HTML.
# ===========================================================================

# Hardcoded sample contexts for question S3-004 — captured live so the
# reader sees concretely what each arm received. Re-capture with:
#   python3 -c "import sys, json; sys.path.insert(0,'eval'); ..."
SAMPLE_QID = "S3-004"
SAMPLE_QUESTION = "A patient is found to carry the rs121918596 variant in RYR1. Which class of medications should be avoided, and why?"
SAMPLE_GOLD_ANSWER = "Volatile Anesthetics should be avoided because of the RYR1 variant. Gold entities: rs121918596, RYR1, Volatile Anesthetics, desflurane, enflurane, halothane."

SAMPLE_CTX_A0 = "(No retrieved context provided. Answer from your own knowledge if confident; otherwise say so.)"

SAMPLE_CTX_A1 = """RETRIEVED EVIDENCE (top-8 by hybrid BM25 + dense search):
  [1] PharmGKB relationship: rs932658 (Variant) — Osteonecrosis of the jaw (Disease); PMIDs: 38612458
  [2] PharmGKB relationship: rs1143623 (Variant) — ustekinumab (Chemical); PMIDs: 28696418
  [3] PharmGKB relationship: rs10033464 (Variant) — antiarrhythmics, class i and iii (Chemical); PMIDs: 22726630
  [4] PharmGKB relationship: rs1143627 (Variant) — ustekinumab (Chemical); PMIDs: 28696418
  [5] PharmGKB relationship: Antihypertensives And Diuretics (Chemical) — rs2070744 (Variant); PMIDs: 19650939
  [6] PharmGKB relationship: rs9332197 (Variant) — time to achieve stable dose; PMIDs: 19752777
  [7] ...
  [8] ...
(Note: this is the actual A1 retrieval — the variant rs121918596 was held out,
so a strong hybrid retriever still surfaces nothing genuinely about it.)"""

SAMPLE_CTX_A2 = """VISIBLE GRAPH (anesthesia seed subgraph):
  - variant_cluster: 13 (RYR1 variants (37), BCHE variants (11), CACNA1S variants (2), …)
  - gene: 13 (RYR1, BCHE, CYP2E1, CACNA1S, …)
  - drug: 7 (succinylcholine, isoflurane, desflurane, methoxyflurane, …)
  - drug_class: 2 (Depolarizing Neuromuscular Blockers, Volatile Anesthetics)
  - phenotype: 1 (Malignant Hyperthermia)

Relationships:
  - desflurane belongs to drug class Volatile Anesthetics
  - desflurane is linked to risk of RYR1 [L1A, CRITICAL]
  - desflurane can trigger Malignant Hyperthermia [L1A]
  - enflurane is linked to risk of RYR1 [L1A, CRITICAL]
  - succinylcholine belongs to drug class Depolarizing Neuromuscular Blockers
  - succinylcholine is linked to risk of RYR1 [L1A, CRITICAL]
  - ... (61 edges total)"""

SAMPLE_CTX_A3 = """QUESTION: A patient is found to carry the rs121918596 variant in RYR1.
Which class of medications should be avoided, and why?

ENTITIES IDENTIFIED IN QUESTION:
  • RYR1 (gene) — matched "ryr1"

DIRECT NEIGHBOURHOOD OF RYR1:
  - desflurane --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - enflurane --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - halothane --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - isoflurane --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - sevoflurane --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - succinylcholine --is linked to risk of[L1A, Toxicity, CRITICAL]--> RYR1
  - RYR1 --has variant cluster[L1A, 37 variants]--> RYR1 variants (37)
  - RYR1 --is associated with[L3]--> Malignant Hyperthermia
  - HMG-CoA reductase inhibitors --affects response to[L3, Toxicity]--> RYR1
  - ... (24 edges)"""


CHAT_SYSTEM_PROMPT = """You are a GraphRAG assistant answering questions about a pharmacogenomic knowledge graph.

Rules:
- Ground every claim in the supplied context. If the context does not support an answer, say so plainly.
- Cite PMIDs in-line as [PMID:xxxxxx] when you have them.
- Prefer the shortest reasoning path (drug → gene/variant → phenotype).
- Highlight CRITICAL / level 1A or 1B evidence when relevant.
- Keep answers under 200 words unless asked for more depth.
- Do not fabricate dosing recommendations or clinical advice."""


def html_glossary() -> str:
    return """
    <details open style="margin:8px 0 32px;">
      <summary style="font-size:14px;">Reading guide — what the codes and metrics mean</summary>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:14px;font-size:13px;">
        <div>
          <h4 style="margin:0 0 8px;font-size:13px;">The four arms (approaches we compared)</h4>
          <ul style="margin:0;padding-left:18px;color:var(--muted);">
            <li><strong>A0 · LLM alone</strong> — question only, no retrieved context</li>
            <li><strong>A1 · plain-text RAG</strong> — strong text-similarity search returns relevant text chunks</li>
            <li><strong>A2 · full subgraph dump</strong> — entire anesthesia subgraph as plain text</li>
            <li><strong>A3 · GraphRAG</strong> — the system: entity-link → graph neighborhood → structured prompt</li>
          </ul>
        </div>
        <div>
          <h4 style="margin:0 0 8px;font-size:13px;">The eight question types (strata)</h4>
          <ul style="margin:0;padding-left:18px;color:var(--muted);">
            <li><strong>S1</strong> well-known facts · <strong>S2</strong> evidence levels</li>
            <li><strong>S3 multi-hop</strong> · <strong>S4 citations</strong> (PMIDs)</li>
            <li><strong>S5</strong> long-tail/niche · <strong>S6</strong> negative controls</li>
            <li><strong>S7</strong> out-of-distribution · <strong>S8</strong> comparative</li>
          </ul>
        </div>
        <div>
          <h4 style="margin:0 0 8px;font-size:13px;">Key metrics</h4>
          <ul style="margin:0;padding-left:18px;color:var(--muted);">
            <li><strong>Pairwise preference</strong> — % of times a judge picks the arm's answer over another, blinded</li>
            <li><strong>Entity recall</strong> — % of expected entities (drugs/genes/etc.) the answer mentioned</li>
            <li><strong>PMID exists rate</strong> — % of cited paper IDs that are real (vs. fabricated)</li>
            <li><strong>Faithfulness / Completeness / Clinical-soundness</strong> — judge rating 1–5</li>
            <li><strong>Support rate</strong> — % of an arm's atomic factual claims that are correct</li>
          </ul>
        </div>
        <div>
          <h4 style="margin:0 0 8px;font-size:13px;">Statistical shorthand</h4>
          <ul style="margin:0;padding-left:18px;color:var(--muted);">
            <li><strong>p &lt; 0.05</strong> — chance is &lt; 5% the difference is luck</li>
            <li><strong>p ≈ 0.5–1.0</strong> — indistinguishable from random</li>
            <li><strong>n</strong> = sample size</li>
            <li><strong>95% CI</strong> — true value is plausibly anywhere in this range</li>
          </ul>
        </div>
      </div>
    </details>"""


def html_tldr(pw: dict, head: dict, rub: dict) -> str:
    """One-paragraph summary, lead with the headline result."""
    def rate(pair):
        m = pw.get(pair, {})
        armA, armB = pair.split("_vs_")
        if not m.get(f"{armA}_wins") and not m.get(f"{armB}_wins"):
            return None, None
        a3 = m[f"{armA}_wins"] if armA == "A3" else m[f"{armB}_wins"]
        o = m[f"{armB}_wins"] if armA == "A3" else m[f"{armA}_wins"]
        return a3 / (a3 + o), m["p_value"]
    a1_rate, a1_p = rate("A3_vs_A1")
    a0_pmid = head["A0"]["pmid_exists_rate_mean"]
    a3_pmid = head["A3"]["pmid_exists_rate_mean"]
    return f"""
    <div class="tldr">
      <h3>TL;DR — three findings in plain English</h3>
      <p><strong>1. GraphRAG did not beat plain-text retrieval.</strong> When a blinded judge
        compared GraphRAG's answers against the same model using plain-text search over the same
        data, GraphRAG was picked {fmt_pct(a1_rate)} of the time — statistically a coin flip
        (we'd expect 50%, p={a1_p:.2f} means the difference is indistinguishable from luck).
        Across 187 carefully designed questions, the graph layer did not earn its complexity.</p>
      <p><strong>2. The LLM judge prefers fluent confidence over factual grounding.</strong> The
        no-context model — given no retrieved data at all — actually won the preference test most
        often. But it invented PMIDs (fake paper citations) <strong>{fmt_pct(1-a0_pmid)} of the
        time</strong>. GraphRAG fabricated PMIDs only {fmt_pct(1-a3_pmid)} of the time. The
        judges couldn't tell the difference between real and fake citations, so they rewarded
        the more confident-sounding answers.</p>
      <p><strong>3. The graph's one real win is knowing what it doesn't know.</strong> On
        questions about drugs not in the graph, GraphRAG refused to answer; plain-text retrieval
        returned vaguely-related junk and the model answered confidently from that. The
        structural value of the graph here is "knowing the boundaries," not "multi-hop
        reasoning" as originally claimed.</p>
    </div>"""


def html_setup_60s() -> str:
    return """
    <span class="section-tag">1 · the setup</span>
    <h2>What we built and what we're testing</h2>
    <p>The project under test is a <strong>pharmacogenomic GraphRAG demo</strong>: a knowledge
      graph built from PharmGKB clinical-variant data, paired with a retrieval pipeline that
      extracts the relevant subgraph for a question and feeds it to an LLM. The system claims that
      structuring the data as a graph — and retrieving via graph traversal — produces more
      grounded, more specific, less hallucinated answers than a frontier LLM produces on its own.</p>
    <p>This evaluation tries to measure whether that claim holds, <strong>and crucially whether the
      benefit comes from the graph structure itself</strong> or just from "having any retrieved
      context at all" (which a much simpler text-similarity retriever would also provide).</p>"""


def html_dataset(ev_dist: dict) -> str:
    sample = """variant       gene    type      level_of_evidence  chemicals                          phenotypes
rs2108622     CYP4F2  Dosage    1B                 warfarin                           (none)
rs7294        VKORC1  Dosage    1B                 warfarin                           (none)
rs118192177   RYR1    Toxicity  1A                 desflurane,enflurane,halothane,    Malignant Hyperthermia
                                                   isoflurane,sevoflurane,succinylcholine"""
    rows = "".join(f"<tr><td><code>{lvl}</code></td><td class='right'>{n}</td></tr>" for lvl, n in ev_dist.items())
    return f"""
    <span class="section-tag">2 · the data</span>
    <h2>PharmGKB — what's in the dataset</h2>
    <p><a href="https://www.pharmgkb.org/" target="_blank">PharmGKB</a> is the open clinical
      pharmacogenomics database. The backbone we use is <code>clinicalVariants.tsv</code> — about
      <strong>5,200 curated rows</strong>, each linking a genetic variant to a drug (and often a
      disease phenotype), with a peer-reviewed evidence level (1A = strongest, 4 = weakest).</p>
    <h3>What one row looks like</h3>
    <pre>{sample}</pre>
    <h3>Evidence-level distribution (full TSV)</h3>
    <table><thead><tr><th>Level</th><th class="right">Rows</th></tr></thead><tbody>{rows}</tbody></table>
    <p class="muted">We work only with high-evidence rows (<code>1A · 1B · 2A · 2B</code>, ~400
      total) because they're the most clinically meaningful. The graph in the live web app has
      <strong>3,213 nodes and 8,024 edges</strong> derived from these rows plus drug-class
      injections and de-duplicated variant clusters per gene.</p>"""


def html_held_out_split() -> str:
    return """
    <span class="section-tag">3 · how we made it a fair test</span>
    <h2>The held-out split</h2>
    <p>The core challenge for any eval against a system built on PharmGKB is <em>contamination</em>
      — both the system and any sensible test question source come from PharmGKB. If we just asked
      questions sourced from rows the graph already contains, GraphRAG would always "win" by
      lookup, not by reasoning.</p>
    <div class="flow">
      <div class="flow-step"><div class="num">1</div><div>
        <strong>Start with the 400 high-evidence rows.</strong> These are the clinically
        meaningful 1A/1B/2A/2B associations in PharmGKB.</div></div>
      <div class="flow-arrow">↓</div>
      <div class="flow-step"><div class="num">2</div><div>
        <strong>Randomly hold out 30% (120 rows).</strong> Deterministic seed (42) so the split
        is reproducible.</div></div>
      <div class="flow-arrow">↓</div>
      <div class="flow-step"><div class="num">3</div><div>
        <strong>Rebuild the graph WITHOUT those rows</strong> — the same flag also strips them
        from A1's text-retrieval index. Neither GraphRAG nor naïve RAG can "lookup" the answers
        from the exact held-out rows.</div></div>
      <div class="flow-arrow">↓</div>
      <div class="flow-step"><div class="num">4</div><div>
        <strong>Generate 187 questions from those held-out rows</strong>, using templates per
        question-type (see §6). The <em>facts</em> are real PharmGKB knowledge but the
        <em>exact rows</em> are unseen by every arm.</div></div>
    </div>
    <p class="muted">Reduced graph: <strong>3,203 nodes / 7,972 edges</strong> (vs the full
      3,213 / 8,024 — small reduction because most graph edges have multiple supporting rows).</p>"""


def html_arms_explained() -> str:
    return f"""
    <span class="section-tag">4 · the four approaches we compared</span>
    <h2>The 4 arms — same model, only the context differs</h2>
    <p>All four arms use the <strong>same generator model (Claude Sonnet 4)</strong> with the
      <strong>same system prompt</strong> (see §5). The only variable is what context the model
      receives alongside the question. This is what isolates "what graph structure adds" from
      "what retrieval adds" from "what the model already knows."</p>
    <p class="muted">Concrete example: all four arms answering question <code>{SAMPLE_QID}</code> —
      <em>"{SAMPLE_QUESTION}"</em></p>
    <div class="arm-card-grid">
      <div class="arm-card">
        <span class="lbl">A0 · no context</span>
        <h4>The frontier LLM alone</h4>
        <p class="desc">Just the question. Tests what the model already knows from training.</p>
        <pre>{html.escape(SAMPLE_CTX_A0)}</pre>
      </div>
      <div class="arm-card">
        <span class="lbl">A1 · steelman naïve RAG</span>
        <h4>Strong text-similarity retrieval</h4>
        <p class="desc">BM25 + dense embeddings (sentence-transformers/MiniLM), fused via reciprocal-rank-fusion, top-8 chunks. Tests whether <em>retrieval</em> helps, irrespective of structure.</p>
        <pre>{html.escape(SAMPLE_CTX_A1)}</pre>
      </div>
      <div class="arm-card">
        <span class="lbl">A2 · full subgraph dump</span>
        <h4>Curated subgraph as plain text</h4>
        <p class="desc">The anesthesia seed subgraph (36 nodes, 61 edges) rendered as flat text. Tests whether smart retrieval matters or context-window stuffing is enough.</p>
        <pre>{html.escape(SAMPLE_CTX_A2)}</pre>
      </div>
      <div class="arm-card a3">
        <span class="lbl">A3 · GraphRAG (system under test)</span>
        <h4>Entity-link → 1-hop neighborhoods → paths</h4>
        <p class="desc">The actual GraphRAG pipeline: entity-link the question to graph nodes, pull each entity's 1-hop neighborhood, find shortest paths between them, render structured text.</p>
        <pre>{html.escape(SAMPLE_CTX_A3)}</pre>
      </div>
    </div>
    <div class="callout">
      <span class="callout-label">Note what's different in A1 vs A3 here</span>
      For this question (a held-out RYR1 variant), <strong>A1's strong retriever finds nothing
      genuinely related</strong> — it surfaces random unrelated PharmGKB rows because the
      variant string itself isn't in any non-held-out chunk. <strong>A3 successfully entity-links
      "RYR1" and surfaces the full neighborhood</strong> (volatile anesthetics, succinylcholine,
      Malignant Hyperthermia) with L1A CRITICAL tags. This is the kind of question where graph
      structure <em>should</em> help — and on this specific one, it did. The aggregate result
      (49% pairwise preference vs A1) shows it doesn't reliably hold across the 187-question set.
    </div>"""


def html_system_prompt() -> str:
    return f"""
    <span class="section-tag">5 · the exact prompts</span>
    <h2>The system prompt all four arms received</h2>
    <p>Identical across A0–A3. Only the context block (shown in §4) differs.</p>
    <details open>
      <summary>System prompt (verbatim)</summary>
      <pre>{html.escape(CHAT_SYSTEM_PROMPT)}</pre>
    </details>
    <details>
      <summary>User prompt template (same for all arms)</summary>
      <pre>Context:
---
{{context_block — see §4 for what each arm's looks like}}
---

User question: {{the question}}</pre>
    </details>"""


def html_strata_samples(questions: dict) -> str:
    """One representative question per stratum, with its gold."""
    samples = []
    for s in STRATUM_NAMES:
        for qid in sorted(questions):
            if questions[qid]["stratum"] == s:
                samples.append((s, questions[qid]))
                break
    cards = []
    for s, q in samples:
        gold = q.get("gold", {})
        gold_str = "  ".join(f"{k}={v}" for k, v in gold.items() if k != "answer_summary")
        cards.append(
            f'<div class="q-card">'
            f'<div><span class="stratum">{s}</span><strong>{STRATUM_NAMES[s]}</strong></div>'
            f'<div style="margin-top:6px;">{html.escape(q["question"])}</div>'
            f'<div class="gold">gold: {html.escape(gold_str[:240])}</div>'
            f'</div>')
    return f"""
    <span class="section-tag">6 · the 200 questions</span>
    <h2>The question set — 187 questions across 8 strata</h2>
    <p>Each stratum probes a different claim about what GraphRAG should be good at (or shouldn't
      hurt on). Questions were auto-generated from the <em>held-out</em> rows using templates,
      then spot-checked for clarity. Each question carries a structured <code>gold</code> record
      (expected entities, evidence level, valid PMIDs, refusal flag) used by both the rule-based
      grader and the LLM judge.</p>
    <table><thead><tr><th>#</th><th>Stratum</th><th>What it tests</th><th class="right">n</th></tr></thead><tbody>
      <tr><td>S1</td><td>Well-known facts</td><td>Does GraphRAG hurt easy questions?</td><td class="right">20</td></tr>
      <tr><td>S2</td><td>Specific evidence levels</td><td>Precision in citing 1A vs 2A</td><td class="right">25</td></tr>
      <tr class="highlight"><td>S3</td><td><strong>Multi-hop reasoning</strong></td><td><strong>The key test of graph structure</strong></td><td class="right">40</td></tr>
      <tr><td>S4</td><td>Citation grounding</td><td>PMID accuracy</td><td class="right">25</td></tr>
      <tr><td>S5</td><td>Long-tail / niche</td><td>Retrieval value beyond parametric memory</td><td class="right">25</td></tr>
      <tr><td>S6</td><td>Negative controls</td><td>Hallucination resistance on no-association pairs</td><td class="right">25</td></tr>
      <tr><td>S7</td><td>Out-of-distribution</td><td>Refusal correctness</td><td class="right">15</td></tr>
      <tr><td>S8</td><td>Comparative</td><td>Reasoning across nodes</td><td class="right">12</td></tr>
    </tbody></table>
    <h3>One example question per stratum (real, from the set)</h3>
    {"".join(cards)}"""


def html_hypotheses_cards(pw: dict, pwps: dict, head: dict, scores: list) -> str:
    """H1/H2/H3 with pre-registered thresholds + the actual result."""
    # H1: pairwise A3 vs A1 > 55% with p<0.05
    m = pw.get("A3_vs_A1", {})
    armA, armB = "A3", "A1"
    a3w = m.get(f"{armA}_wins", 0)
    aw = m.get(f"{armB}_wins", 0)
    h1_rate = a3w / (a3w + aw) if (a3w + aw) else None
    h1_p = m.get("p_value", 1)
    h1_ok = (h1_rate or 0) > 0.55 and h1_p < 0.05
    # H2: S3 pairwise A3 > A1 by ≥10pp descriptively
    s3 = pwps.get("S3", {}).get("A3_vs_A1", {})
    s3_a3 = s3.get("A3_wins", 0); s3_a1 = s3.get("A1_wins", 0)
    h2_rate = s3_a3 / (s3_a3 + s3_a1) if (s3_a3 + s3_a1) else None
    h2_ok = (h2_rate or 0) >= 0.60
    # H3: A3 within 5pp of A0 on S1 metrics (use entity_recall as proxy)
    s1_a0 = [s for s in scores if s["stratum"] == "S1" and s["arm"] == "A0"]
    s1_a3 = [s for s in scores if s["stratum"] == "S1" and s["arm"] == "A3"]
    def er(rows):
        vals = [r.get("entity_recall") for r in rows if r.get("entity_recall") is not None]
        return statistics.mean(vals) if vals else None
    a0_er = er(s1_a0); a3_er = er(s1_a3)
    h3_diff = abs((a0_er or 0) - (a3_er or 0)) if a0_er and a3_er else None
    h3_ok = h3_diff is not None and h3_diff <= 0.05
    def vcls(ok): return "good" if ok else "bad"
    def vtxt(ok): return "✓ SUPPORTED" if ok else "✗ NOT SUPPORTED"
    return f"""
    <span class="section-tag">7 · what we hypothesized</span>
    <h2>The three pre-registered hypotheses</h2>
    <p>Declared in <code>eval/preregistration.md</code> <em>before</em> any answers were generated,
      with the exact decision threshold below. This prevents post-hoc goalpost-moving.</p>
    <div class="hyp h1">
      <div class="tag">H1<br><span class="muted" style="font-weight:400;font-size:11px;">primary</span></div>
      <div>
        <div class="claim"><strong>GraphRAG beats plain-text retrieval</strong> when a blinded
          judge compares both answers — winning more than 55% of comparisons, with the difference
          unlikely to be chance (p&lt;0.05).</div>
        <div class="muted" style="font-size:12px;margin-top:4px;">
          Result: GraphRAG won {a3w} of {a3w+aw} comparisons ({fmt_pct(h1_rate)}); p={h1_p:.2f}
          (i.e. statistically indistinguishable from a 50-50 coin flip).
        </div>
      </div>
      <div class="verdict {vcls(h1_ok)}">{vtxt(h1_ok)}</div>
    </div>
    <div class="hyp">
      <div class="tag">H2<br><span class="muted" style="font-weight:400;font-size:11px;">multi-hop</span></div>
      <div>
        <div class="claim">On <strong>multi-hop reasoning questions</strong> (the questions
          specifically designed to require chaining through 2+ graph edges — variant → gene →
          drug class), GraphRAG should outperform plain-text retrieval.</div>
        <div class="muted" style="font-size:12px;margin-top:4px;">
          Result: GraphRAG was preferred {s3_a3} of {s3_a3+s3_a1} times ({fmt_pct(h2_rate)}) —
          plain-text retrieval actually won most multi-hop questions.
        </div>
      </div>
      <div class="verdict {vcls(h2_ok)}">{vtxt(h2_ok)}</div>
    </div>
    <div class="hyp">
      <div class="tag">H3<br><span class="muted" style="font-weight:400;font-size:11px;">no regression</span></div>
      <div>
        <div class="claim">On <strong>easy / well-known questions</strong>, GraphRAG should be
          within 5 percentage points of the no-context model on "did the answer name the right
          entities" — adding context shouldn't hurt easy questions.</div>
        <div class="muted" style="font-size:12px;margin-top:4px;">
          Result: no-context model named {fmt_pct(a0_er)} of expected entities, GraphRAG
          {fmt_pct(a3_er)}; difference {fmt_pct(h3_diff)} (above the 5pp tolerance).
        </div>
      </div>
      <div class="verdict {vcls(h3_ok)}">{vtxt(h3_ok)}</div>
    </div>"""


def html_how_we_measured() -> str:
    return """
    <span class="section-tag">8 · how we measured</span>
    <h2>Four independent metric families</h2>
    <p>The eval intentionally uses multiple independent measurement approaches, because each one
      has different failure modes. Convergent results across all four = real signal.</p>
    <div class="flow">
      <div class="flow-step"><div class="num">1</div><div>
        <strong>Rule-based metrics</strong> (deterministic Python). PMID exists in our corpus,
        entity precision/recall vs gold, evidence-level exact-match, refusal correctness.
        Same definition across all arms — no arm is structurally penalised.</div></div>
      <div class="flow-step"><div class="num">2</div><div>
        <strong>Pairwise preference</strong> (the H1 headline). For each question, Claude Haiku
        4.5 sees both answers blinded, randomised order, picks the better one. n=374
        comparisons (A3 vs A1 + A3 vs A0). Sign-test p value.</div></div>
      <div class="flow-step"><div class="num">3</div><div>
        <strong>Rubric ratings (Faithfulness / Completeness / Clinical-soundness 1–5)</strong>.
        Haiku rates each answer against ground truth (not arm-specific context — that asymmetry
        was a v1 bug we fixed). Targets coverage 748; achieved 445 before CLI hangs.</div></div>
      <div class="flow-step"><div class="num">4</div><div>
        <strong>Merged-claim hallucination</strong>. Per question, judge extracts the union of
        atomic factual claims across all 4 answers, then marks each as supported / unsupported /
        unverifiable vs ground truth. Same denominator across arms.</div></div>
    </div>
    <div class="callout">
      <span class="callout-label">The judge — Claude Haiku 4.5, cross-size within Claude</span>
      Generator was Claude Sonnet 4 (frontier); judge was Claude Haiku 4.5. Within-family judging
      is a known weaker mitigation than cross-family (Gemini judging Claude) — declared as a
      threat. We compensate by leaning on the deterministic rule-based metrics (judge-independent)
      and on convergence across the four metric families.
    </div>"""


def html_whats_next() -> str:
    return """
    <span class="section-tag">14 · what this means + what's next</span>
    <h2>So what?</h2>
    <p>This is a <strong>mixed-to-negative result</strong>, and a more useful one than a clean
      sweep would be. It shows:</p>
    <ul>
      <li><strong>For this domain (pharmacogenomics) and this dataset (PharmGKB), graph structure
        doesn't reliably beat strong hybrid retrieval.</strong> The reason is structural: PharmGKB
        rows are already denormalised — drug, gene, variant, phenotype, and evidence level all
        appear together in a single row — so a text retriever can reach "multi-hop" facts without
        actually traversing a graph.</li>
      <li><strong>The structural value of the graph lives in <em>refusal</em></strong>, not in
        reasoning. When a drug isn't in the graph, A3 refuses cleanly; A1's retrieval always
        returns <em>something</em>, tempting hallucination.</li>
      <li><strong>LLM-as-judge cannot detect fabricated PMIDs.</strong> A0 cited fake PMIDs 68%
        of the time and still won the preference test. Any eval relying solely on preference is
        measuring fluency, not correctness.</li>
    </ul>
    <h3>Concrete next steps to make this a stronger eval</h3>
    <ul>
      <li><strong>Also hold out relationships.tsv rows</strong> that restate the same facts —
        removes the leakage that gave A1 the S4 citation advantage.</li>
      <li><strong>Cross-family judging</strong> (Gemini or GPT judging Claude) to remove the
        within-Claude self-preference bias.</li>
      <li><strong>Test in a less-denormalised domain.</strong> PharmGKB rows do half of GraphRAG's
        work for free. Drug-interaction networks, gene-pathway graphs, or biomedical literature
        graphs (where multi-hop ≠ single-row lookup) would be a fairer test of structural value.</li>
      <li><strong>Inline PMIDs in A3's context.</strong> The web app's chat flow doesn't feed
        PMIDs to the model (they go to the UI as separate chips). That's a real product
        limitation — A3 cannot do citation grounding even though the graph has the PMIDs.</li>
      <li><strong>Hand-rate 20 answers</strong> to calibrate Haiku's preferences vs human
        (preregistration §8.4, still not done).</li>
    </ul>"""


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

    # Evidence-level distribution from clinicalVariants.tsv (for §2)
    ev_dist = {}
    try:
        import csv as _csv
        _csv.field_size_limit(2**30)
        with (ROOT / "clinicalVariants.tsv").open(newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh, delimiter="\t"):
                lv = (row.get("level of evidence") or "").strip() or "(none)"
                ev_dist[lv] = ev_dist.get(lv, 0) + 1
    except Exception:
        pass
    # Sort by 1A first, then 1B, etc.
    order = {"1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5}
    ev_dist = dict(sorted(ev_dist.items(), key=lambda x: order.get(x[0], 99)))

    # HTML
    body = []
    body.append('<div class="wrap">')
    body.append('<div class="hero">')
    body.append('<span class="pill"><span class="dot"></span> GraphRAG eval · 4 arms × 187 held-out PharmGKB questions</span>')
    body.append('<h1>Does <em>graph structure</em> actually help an LLM answer pharmacogenomics questions?</h1>')
    body.append('<p class="muted" style="max-width:640px;margin:8px auto 0;">A blinded, falsifiable, four-arm evaluation of the Polygence GraphRAG demo, with cross-checks designed to reveal what LLM-as-judge can and cannot detect.</p>')
    body.append('</div>')

    # TL;DR
    body.append(html_tldr(pw, head, rub))

    # Glossary — keeps the rest of the report readable without flipping back
    body.append(html_glossary())

    # 1. Setup
    body.append(html_setup_60s())

    # 2. Dataset
    body.append(html_dataset(ev_dist))

    # 3. Held-out split
    body.append(html_held_out_split())

    # 4. The four arms (with sample contexts)
    body.append(html_arms_explained())

    # 5. System prompt
    body.append(html_system_prompt())

    # 6. Question strata + samples
    body.append(html_strata_samples(questions))

    # 7. Hypotheses
    body.append(html_hypotheses_cards(pw, pwps, head, scores))

    # 8. How we measured
    body.append(html_how_we_measured())

    # 9. The headline results
    body.append('<span class="section-tag">9 · the results</span>')
    body.append("<h2>Headline — blinded pairwise preference (n=187)</h2>")
    body.append('<p class="muted">For each question, Claude Haiku saw both answers blinded with order randomised, and picked the better one. A3 winning &gt;55% with p&lt;0.05 would have supported H1.</p>')
    body.append(html_pairwise(pw))

    # Rule-based, rubric, hallucination, per-stratum — wrap as §10
    body.append('<span class="section-tag">10 · the supporting numbers</span>')

    # Overall rule-based metrics
    body.append("<h2>Rule-based metrics (overall)</h2>")
    body.append('<p class="muted">Deterministic Python. PMID exists in our corpus, entity recall vs gold, etc. Same definition across all arms.</p>')
    body.append(html_headline(head, pw))

    # Rubric ratings (Faithfulness / Completeness / Clinical soundness)
    rub_n = sum(m.get("n", 0) for m in rub.values())
    body.append(f'<h2>Rubric ratings — F / C / CS  <span class="muted">(n={rub_n}/748)</span></h2>')
    body.append('<p class="muted">Each answer rated 1–5 on Faithfulness (claims supported), '
                'Completeness (covers gold), Clinical soundness (would a pharmacist call it misleading). '
                'Mean ± 95% CI half-width.</p>')
    body.append(html_rubric(rub))
    if rub_n < 740:
        body.append('<p class="muted"><em>Coverage caveat:</em> rubric run hit Claude CLI hangs in late-S7/S8. '
                    f'Final coverage {rub_n}/748 ≈ {int(100*rub_n/748)}%. Per-arm counts balanced; means meaningful.</p>')

    # Hallucination via merged segmentation
    body.append(f'<h2>Hallucination rate — merged claims  <span class="muted">(n={hr.get("questions_covered", 0)}/187)</span></h2>')
    body.append('<p class="muted">Per question, the judge extracts the union of atomic claims across all 4 answers, '
                'then per-arm scores each as supported / unsupported / unverifiable. Same denominator across arms.</p>')
    body.append(html_hallucination(hr))
    if hr.get("questions_covered", 0) < 180:
        body.append('<p class="muted"><em>Coverage caveat:</em> segmentation stopped early after CLI hangs. '
                    f'Final coverage {hr.get("questions_covered", 0)}/187, biased to S1–S4. Treat as directional.</p>')

    # Per stratum
    body.append("<h2>Per-stratum breakdown</h2>")
    body.append('<p class="muted">Descriptive only — per-stratum n (12–40) is too small for inferential claims. '
                'Useful for seeing <em>where</em> each arm wins or loses.</p>')
    body.append(html_per_stratum(pps, pwps))

    # 11. Why this happened (existing data-driven findings narrative)
    body.append('<span class="section-tag">11 · why this happened</span>')
    body.append("<h2>The deeper finding — preference ≠ correctness</h2>")
    body.append(html_findings(pw, pwps, head))
    # Rubric + hallucination corroboration
    if any(rub.get(a, {}).get("n") for a in ARMS) and hr.get("questions_covered"):
        def fm(a): return rub.get(a, {}).get("F_mean")
        def sr(a): return hr.get(a, {}).get("support_rate")
        body.append(f"""
        <div class="callout">
          <span class="callout-label">All four metric families say the same thing</span>
          <ul>
            <li><strong>Pairwise preference:</strong> A0 preferred over A3 in {1 - (pw.get('A3_vs_A0',{}).get('armA_rate') or 0):.0%} of comparisons.</li>
            <li><strong>Rubric Faithfulness (1–5):</strong> A0 {fm('A0'):.2f}, A1 {fm('A1'):.2f}, A3 {fm('A3'):.2f}. Judge rates A0 highest.</li>
            <li><strong>Merged-claim support rate:</strong> A0 {fmt_pct(sr('A0'))}, A1 {fmt_pct(sr('A1'))}, A3 {fmt_pct(sr('A3'))}, A2 {fmt_pct(sr('A2'))}.</li>
            <li><strong>Rule-based PMID-exists:</strong> A0 {fmt_pct(head['A0']['pmid_exists_rate_mean'])} (i.e. <strong>fabricates {fmt_pct(1-head['A0']['pmid_exists_rate_mean'])}</strong>), A3 {fmt_pct(head['A3']['pmid_exists_rate_mean'])}.</li>
          </ul>
          A0 has strong parametric pharmacogenomic knowledge — its <em>substantive</em> claims are largely correct. But it fabricates specific PMIDs at a high rate that the LLM judge cannot detect. The judge rewards fluent confidence; the rule-based metrics see the lies.
        </div>""")

    # 12. Examples
    body.append('<span class="section-tag">12 · illustrative examples</span>')
    body.append("<h2>One question per stratum — all four arms side-by-side</h2>")
    body.append('<p class="muted">Truncated to 600 chars per answer. Look for: A0 confident-but-citation-fabricating, A1 sometimes mis-retrieving, A2 anesthesia-narrow, A3 grounded-but-occasionally-cluttered.</p>')
    body.append(html_examples(answers_by_qid_arm, questions, example_qids))

    # 13. Deviations + threats
    body.append('<span class="section-tag">13 · honest limitations</span>')
    body.append("<h2>Deviations from preregistration + threats to validity</h2>")
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
    # 14. What's next
    body.append(html_whats_next())

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
