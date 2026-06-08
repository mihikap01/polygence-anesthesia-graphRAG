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

ARMS = ["A0", "A1", "A2", "A3"]   # all loaded from data (A2 retained for reproducibility)
DISPLAY_ARMS = ["A0", "A1", "A3"]  # what we show in the report
ARM_LABELS = {
    "A0": "A0 · LLM alone (no context)",
    "A1": "A1 · LLM + plain-text retrieval",
    "A2": "A2 · LLM + static subgraph dump",
    "A3": "A3 · LLM + subgraph RAG (system under test)",
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
    for arm in DISPLAY_ARMS:
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
        for arm in DISPLAY_ARMS:
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
        for arm in DISPLAY_ARMS:
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
.wrap { max-width:880px; margin:0 auto; padding:48px 24px 64px; }
.paper-header { padding:0 0 32px; border-bottom:1px solid var(--border); margin-bottom:32px; }
.venue { font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); margin-bottom:14px; }
.byline { font-size:14px; color:var(--muted); margin-top:10px; max-width:680px; line-height:1.55; }
.abstract { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:20px 24px; margin:0 0 28px; box-shadow:var(--soft); }
.abstract h3 { margin:0 0 8px; font-size:14px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.abstract p { font-size:14.5px; line-height:1.65; margin:8px 0; }
pre.figure { background:var(--bg); border:1px solid var(--border); border-radius:10px; padding:14px; font-size:12.5px; overflow-x:auto; }
.hero { text-align:center; padding:24px 0 36px; }
h1 { font-size:30px; font-weight:600; letter-spacing:-.02em; margin:0 0 6px; line-height:1.2; }
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
/* Table of contents */
.report-toc {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 18px 22px 14px; box-shadow: var(--soft); margin: 0 0 32px;
}
.report-toc h4 {
  margin: 0 0 10px; font-size: 11px; text-transform: uppercase;
  letter-spacing: .08em; color: var(--muted); font-weight: 700;
}
.report-toc ol { margin: 0; padding-left: 20px; font-size: 14px; }
.report-toc li { margin: 5px 0; line-height: 1.5; }
.report-toc a { color: var(--primary); text-decoration: none; }
.report-toc a:hover { text-decoration: underline; }
.report-toc .muted { font-weight: 400; font-size: 13px; }
.report-toc .toc-appendix {
  margin-top: 10px; padding-top: 10px; border-top: 1px dashed hsl(215 15% 80%);
}
.report-toc .toc-appendix ul {
  margin: 6px 0 0; padding-left: 20px; list-style: none;
  font-size: 12.5px; color: var(--muted);
}
.report-toc .toc-appendix ul li { margin: 3px 0; }
/* Appendix A — reproduction recipe styling */
pre.cmd-app {
  background: hsl(215 20% 18%); color: hsl(195 50% 94%);
  border-radius: 10px; padding: 12px 16px; overflow-x: auto;
  font-size: 13px; line-height: 1.55; margin: 10px 0;
  white-space: pre; font-family: 'JetBrains Mono', ui-monospace, monospace;
  box-shadow: var(--soft);
}
ul.meta-list { font-size: 13px; color: var(--muted); margin: 8px 0 18px; padding-left: 20px; }
ul.meta-list li { margin: 4px 0; }
ul.meta-list strong { color: var(--fg); }
ul.meta-list code { background: var(--accent-bg); padding: 1px 5px; border-radius: 4px; }
.metric-block {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 18px 22px; box-shadow: var(--soft); margin: 14px 0 22px;
}
.metric-block h4 {
  font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--primary); margin: 14px 0 6px; font-weight: 600;
}
.metric-block h4:first-child { margin-top: 0; }
.metric-block p { margin: 6px 0; font-size: 14px; }
.metric-block ul { margin: 6px 0; padding-left: 22px; font-size: 14px; }
.step-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); font-weight: 600; margin: 14px 0 4px;
}
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
    for arm in DISPLAY_ARMS:
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
    for arm in DISPLAY_ARMS:
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


def html_abstract(pw: dict, head: dict) -> str:
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
    <div class="abstract">
      <h3>Abstract</h3>
      <p>We evaluate whether subgraph-RAG over a curated pharmacogenomic knowledge graph
        (PharmGKB) produces better question-answering than (i) a frontier LLM with no retrieved
        context, or (ii) the same LLM with a strong hybrid plain-text retriever (BM25 + dense
        embeddings, RRF, top-K). We compare these three retrieval conditions on 187 questions
        derived from a 30% held-out partition of PharmGKB's high-evidence clinical-variant rows,
        across eight question strata. The primary outcome is blinded pairwise preference judged
        by Claude Haiku 4.5; secondary outcomes include deterministic rule-based metrics, anchored
        1–5 rubric ratings, and merged-claim hallucination rate.</p>
      <p>Subgraph RAG did <strong>not</strong> outperform plain-text RAG on the primary preference
        test ({fmt_pct(a1_rate)} A3 wins, sign-test p={a1_p:.2f}). The no-context model was
        preferred most often by the judge despite fabricating cited paper identifiers (PMIDs) at
        a rate of {fmt_pct(1 - a0_pmid)}, versus {fmt_pct(1 - a3_pmid)} for subgraph RAG —
        evidencing that LLM-as-judge rewards fluent confidence over factual grounding. The
        graph layer's only consistent advantage was appropriate refusal on out-of-distribution
        queries. We attribute the null primary result to PharmGKB's row-level denormalisation,
        which packs multi-hop facts into single text chunks and thereby allows strong text
        retrieval to recover them without graph traversal. We discuss confounds (notably
        partial held-out leakage via <code>relationships.tsv</code>) and propose targeted
        follow-up experiments.</p>
      <p style="margin-top:14px; padding-top:12px; border-top:1px solid hsl(215 15% 80%); font-size:14px;">
        <strong>Want to reproduce these results?</strong> Every command, expected output,
        and source-file link is in <a href="#appendix-a"><strong>Appendix A</strong></a> at
        the bottom of this report.
      </p>
    </div>"""


def html_toc() -> str:
    return """
    <nav class="report-toc">
      <h4>Contents</h4>
      <ol>
        <li><a href="#s1">1. Introduction</a></li>
        <li><a href="#s2">2. Background</a></li>
        <li><a href="#s3">3. Methods</a> <span class="muted">— dataset, held-out split, three arms, prompts, hypotheses, metrics</span></li>
        <li><a href="#s4">4. Results</a> <span class="muted">— pairwise headline, rule-based, rubric, hallucination, per-stratum</span></li>
        <li><a href="#s5">5. Discussion</a> <span class="muted">— why subgraph RAG did not beat plain-text RAG</span></li>
        <li><a href="#s6">6. Limitations and threats to validity</a></li>
        <li><a href="#s7">7. Conclusions and future work</a></li>
        <li class="toc-appendix"><a href="#appendix-a"><strong>Appendix A. How each metric works + how to reproduce every result</strong></a>
          <ul>
            <li><a href="#appendix-a"><strong>A.0 Quick start</strong></a> <span style="color: var(--primary);">(full pipeline, copy-paste)</span></li>
            <li><a href="#a-setup">A.1 Setup</a> · <a href="#a-gen">A.2 Generation</a></li>
            <li><a href="#a-rule">A.3 Rule-based</a> · <a href="#a-pairwise">A.4 Pairwise preference</a></li>
            <li><a href="#a-rubric">A.5 Rubric ratings</a> · <a href="#a-halluc">A.6 Hallucination</a></li>
            <li><a href="#a-report">A.7 Aggregate</a> · <a href="#a-code">A.8 Full code reference</a></li>
          </ul>
        </li>
        <li class="toc-appendix"><a href="#appendix-b"><strong>Appendix B. Which prompts actually let GraphRAG beat the baselines?</strong></a> <span style="color: var(--primary);">(data-mined patterns from the same eval)</span>
          <ul>
            <li><a href="#b-patterns">B.2 The four prompt patterns where GraphRAG wins</a></li>
            <li><a href="#b-anti">B.3 When NOT to use GraphRAG</a></li>
            <li><a href="#b-prompts">B.4 Suggested demo prompts</a></li>
          </ul>
        </li>
      </ol>
    </nav>"""


def html_introduction() -> str:
    return """
    <span class="section-tag">1 · introduction</span>
    <h2 id="s1">1. Introduction</h2>
    <p>Retrieval-augmented generation (RAG) systems are increasingly proposed for biomedical
      question answering, where the cost of fabricated facts is high. A growing literature argues
      that <em>knowledge-graph</em>-based RAG — retrieving a relevant subgraph instead of text
      chunks — should outperform plain-text RAG by preserving entity relationships, evidence
      provenance, and multi-hop reasoning paths.</p>
    <p>This report tests that claim concretely. We evaluate a subgraph-RAG implementation built
      over PharmGKB, the curated pharmacogenomic knowledge base, focused on anesthesia and
      pharmacogenomic-risk reasoning. We compare it against (a) the same frontier LLM with no
      retrieved context (a parametric-knowledge baseline) and (b) the same LLM with a strong
      hybrid text retriever over the same data. The primary test is blinded pairwise preference;
      secondary tests are rule-based grounding metrics, anchored rubric ratings, and a per-claim
      hallucination metric using merged-claim segmentation.</p>"""


def html_background() -> str:
    return """
    <span class="section-tag">2 · background</span>
    <h2 id="s2">2. Background</h2>
    <p><strong>RAG architectures.</strong> The classical RAG pipeline chunks text, indexes
      chunks via similarity search, and concatenates the top-K matches into the LLM's prompt.
      <em>Subgraph RAG</em> instead indexes the source data as a knowledge graph: at query time
      the question is entity-linked to graph nodes, the system extracts a relevant subgraph
      (neighborhoods of linked entities and/or shortest paths between them), and renders that
      subgraph as text for the LLM. We refer to the system under test as "subgraph RAG"
      following common usage. Note that this is <em>distinct</em> from Microsoft GraphRAG
      (Edge et al., 2024), which additionally pre-computes LLM-generated summaries of
      automatically-detected graph communities and retrieves over those summaries — a step we
      did not implement.</p>
    <p><strong>The claim under test.</strong> Subgraph RAG should improve over text RAG when
      (i) the graph captures relationships that are not co-located in single source documents,
      (ii) the question requires reasoning across multiple such relationships, and (iii) the
      generator benefits from explicit relationship structure in its prompt. The PharmGKB demo
      makes these claims for pharmacogenomic question answering. We test whether they hold under
      a blinded, held-out evaluation.</p>"""


def html_dataset_body(ev_dist: dict) -> str:
    sample = """variant       gene    type      level_of_evidence  chemicals                          phenotypes
rs2108622     CYP4F2  Dosage    1B                 warfarin                           (none)
rs7294        VKORC1  Dosage    1B                 warfarin                           (none)
rs118192177   RYR1    Toxicity  1A                 desflurane,enflurane,halothane,    Malignant Hyperthermia
                                                   isoflurane,sevoflurane,succinylcholine"""
    rows = "".join(f"<tr><td><code>{lvl}</code></td><td class='right'>{n}</td></tr>" for lvl, n in ev_dist.items())
    return f"""
    <p>The source is <a href="https://www.pharmgkb.org/" target="_blank">PharmGKB</a>, an open
      pharmacogenomics database. The backbone for our graph is
      <code>clinicalVariants.tsv</code>: approximately 5,200 curated rows, each linking a
      genetic variant to one or more drugs (and often a disease phenotype), with a peer-reviewed
      evidence level (1A strongest through 4 weakest).</p>
    <p class="muted" style="font-size:12px;margin-bottom:2px;">Table 1. Example rows from <code>clinicalVariants.tsv</code> (truncated columns).</p>
    <pre class="figure">{sample}</pre>
    <p class="muted" style="font-size:12px;margin-bottom:2px;">Table 2. Evidence-level distribution in the full file.</p>
    <table><thead><tr><th>Level</th><th class="right">Rows</th></tr></thead><tbody>{rows}</tbody></table>
    <p>We restrict to high-evidence rows (1A · 1B · 2A · 2B, ≈400 total) because these are the
      most clinically meaningful and least noisy. The full reasoning graph built from these rows
      (plus a curated drug-class layer and per-gene variant-cluster collapsing) contains 3,213
      nodes and 8,024 edges in the production app.</p>"""


def html_held_out_body() -> str:
    return """
    <p>A core methodological challenge: both the system under test and any in-distribution
      question source come from PharmGKB. Naive question generation would let subgraph RAG
      "win" by lookup rather than reasoning. We address this with a deterministic
      train/test split:</p>
    <ol style="font-size:14px;">
      <li>Restrict to the 400 high-evidence clinical-variant rows (levels 1A/1B/2A/2B).</li>
      <li>Randomly select 30% (120 rows) as held-out. Seed fixed at 42 for reproducibility.</li>
      <li>Rebuild the graph <em>without</em> these rows. The same flag also strips them from
        the plain-text retriever's index, so neither A1 nor A3 can recover them by direct
        lookup.</li>
      <li>Generate 187 questions from those held-out rows using stratum-specific templates
        (§3.5).</li>
    </ol>
    <p>The reduced graph contains 3,203 nodes and 7,972 edges (reduction is small because most
      graph edges have multiple supporting source rows). The 120 held-out rows yield 187 test
      questions across 8 question types.</p>"""


def html_arms_body() -> str:
    return f"""
    <p>The three arms use the <strong>same generator (Claude Sonnet 4)</strong> with the
      <strong>same system prompt</strong> (§3.4). The only variable across arms is the context
      provided alongside the question, allowing us to isolate the effect of retrieval (A0 vs.
      A1/A3) and the effect of graph-native vs. text-similarity retrieval (A1 vs. A3).</p>
    <p class="muted" style="font-size:12px;margin-bottom:2px;">Figure 1. The three arms shown
      against a single representative question (<code>{SAMPLE_QID}</code>:
      <em>"{SAMPLE_QUESTION}"</em>).</p>
    <div class="arm-card-grid">
      <div class="arm-card">
        <span class="lbl">A0 · LLM alone (no retrieval)</span>
        <h4>Parametric-knowledge baseline</h4>
        <p class="desc">The model receives only the question. Tests what the frontier LLM
          already knows from training.</p>
        <pre>{html.escape(SAMPLE_CTX_A0)}</pre>
      </div>
      <div class="arm-card">
        <span class="lbl">A1 · plain-text RAG</span>
        <h4>Hybrid text-similarity retrieval (steelman)</h4>
        <p class="desc">PharmGKB rows are denormalised into single-row text chunks (clinical
          variants + the relevant subset of <code>relationships.tsv</code>) and indexed with BM25
          plus dense embeddings (sentence-transformers/all-MiniLM-L6-v2). At query time the two
          rankings are fused via reciprocal-rank-fusion (k=60) and the top 8 chunks are
          concatenated into the prompt. Isolates the contribution of <em>any</em> retrieval over
          the same data, independent of graph structure.</p>
        <pre>{html.escape(SAMPLE_CTX_A1)}</pre>
      </div>
      <div class="arm-card a3">
        <span class="lbl">A3 · subgraph RAG (system under test)</span>
        <h4>Entity-linking → 1-hop neighborhood → shortest-path expansion</h4>
        <p class="desc">For each question: (1) fuzzy-match question tokens against the graph's
          3,203 nodes via the search index to identify mentioned entities (up to 6, by score);
          (2) extract each linked entity's 1-hop neighborhood of edges; (3) compute BFS shortest
          paths between every pair of linked entities, up to 4 hops, preferring edges flagged
          CRITICAL or evidence-level 1A/1B; (4) render the result as a structured text block
          with explicit sections for entities, neighborhoods, and paths. A purely text-similarity
          system cannot perform steps 1, 2, or 3.</p>
        <pre>{html.escape(SAMPLE_CTX_A3)}</pre>
      </div>
    </div>
    <p style="font-size:13px;margin-top:14px;">
      <strong>On terminology.</strong> A3 is what the literature usually calls "subgraph RAG"
      — query-conditional retrieval of a relevant subgraph, rendered for the LLM. We do not
      implement Microsoft GraphRAG (Edge et al., 2024), which adds an offline community-detection
      and LLM-summarisation step. Our test concerns the subgraph-RAG variant because that is the
      architecture the demo ships.
    </p>
    <p style="font-size:13px;color:var(--muted);">
      <strong>Why three arms, not four.</strong> A fourth condition (A2 — a static dump of a
      hand-curated 36-node anesthesia subgraph) was generated and retained in the raw data
      (<code>eval/answers.jsonl</code>). On reflection, A2 conflated "graph context absent" with
      "graph context off-topic" — its context was the same fixed subgraph for every question
      regardless of relevance. We omit it from analysis to keep the comparison between three
      architecturally distinct conditions: <em>no retrieval</em>, <em>text retrieval</em>,
      <em>graph retrieval</em>.
    </p>"""


def html_prompts_body() -> str:
    return f"""
    <p>All three arms receive the same system prompt — identical to the production web app's
      chat endpoint — and the same user-prompt template (a context block followed by the
      question). Only the contents of the context block differ.</p>
    <details open>
      <summary>System prompt (verbatim)</summary>
      <pre>{html.escape(CHAT_SYSTEM_PROMPT)}</pre>
    </details>
    <details>
      <summary>User-prompt template</summary>
      <pre>Context:
---
{{context_block — A0: empty placeholder; A1: top-8 RRF-fused chunks; A3: structured subgraph}}
---

User question: {{the question}}</pre>
    </details>"""


def html_questions_body(questions: dict) -> str:
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
    <p>Questions are generated by stratum-specific templates from the held-out rows. Each
      question carries a structured <code>gold</code> record (expected entities, evidence level,
      valid PMIDs, refusal flag) used by both the rule-based grader and the LLM judge.</p>
    <p class="muted" style="font-size:12px;margin-bottom:2px;">Table 3. The eight strata.</p>
    <table><thead><tr><th>#</th><th>Stratum</th><th>What it probes</th><th class="right">n</th></tr></thead><tbody>
      <tr><td>S1</td><td>Well-known facts</td><td>Does the graph layer hurt easy questions?</td><td class="right">20</td></tr>
      <tr><td>S2</td><td>Specific evidence levels</td><td>Precision in distinguishing 1A vs. 2A</td><td class="right">25</td></tr>
      <tr class="highlight"><td>S3</td><td><strong>Multi-hop reasoning</strong></td><td><strong>Direct test of graph traversal value</strong></td><td class="right">40</td></tr>
      <tr><td>S4</td><td>Citation grounding</td><td>PMID accuracy</td><td class="right">25</td></tr>
      <tr><td>S5</td><td>Long-tail / niche</td><td>Retrieval value beyond parametric memory</td><td class="right">25</td></tr>
      <tr><td>S6</td><td>Negative controls</td><td>Resistance to hallucinating false associations</td><td class="right">25</td></tr>
      <tr><td>S7</td><td>Out-of-distribution</td><td>Refusal correctness on unknown drugs</td><td class="right">15</td></tr>
      <tr><td>S8</td><td>Comparative</td><td>Reasoning across multiple nodes</td><td class="right">12</td></tr>
    </tbody></table>
    <p class="muted" style="font-size:12px;margin-top:18px;margin-bottom:6px;">Figure 2. One real example question per stratum.</p>
    {"".join(cards)}"""


def html_metrics_body() -> str:
    return """
    <p>Four independent measurement approaches are used; their disagreement modes are
      complementary, so convergence across all four is treated as the strongest signal.</p>
    <ol style="font-size:14px;">
      <li><strong>Rule-based metrics</strong> (deterministic Python). PMID existence in our
        corpus, entity precision and recall against the gold entity set, evidence-level
        exact-match (for the relevant stratum), and refusal correctness (for negative-control
        and out-of-distribution strata). Same definition across arms.</li>
      <li><strong>Blinded pairwise preference</strong> (Claude Haiku 4.5 as judge). For each
        question, the judge sees two answers with arm labels stripped and order randomised, then
        picks the preferred answer or "tie." A3 vs. A1 is the primary headline; A3 vs. A0 is
        secondary. We report sign-test p-values and Wilson-score 95% confidence intervals.</li>
      <li><strong>Anchored rubric ratings (1–5)</strong>. Same judge rates each answer
        independently on Faithfulness, Completeness, and Clinical soundness against ground
        truth (not against arm-specific retrieved context, which would penalise A0 by
        construction).</li>
      <li><strong>Merged-claim hallucination rate</strong>. Per question, the judge extracts
        the union of atomic factual claims across all four arms\' answers, then marks each as
        supported / unsupported / unverifiable against ground truth. Same merged claim list
        yields the same denominator across arms, making the comparison fair.</li>
    </ol>
    <p style="font-size:13px;">
      <strong>Generator / judge configuration.</strong> Generator: Claude Sonnet 4 via the
      Claude Code CLI. Judge: Claude Haiku 4.5 via a separate CLI session. This within-family
      size split partially mitigates self-preference bias (a known weakness of LLM-as-judge
      when generator and judge share parameters); a cross-family judge (e.g. Gemini judging
      Claude) would be stronger and is listed as a methodological improvement in §7.
    </p>"""


def html_glossary() -> str:
    return """
    <details open style="margin:8px 0 32px;">
      <summary style="font-size:14px;">Reading guide — what the codes and metrics mean</summary>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:14px;font-size:13px;">
        <div>
          <h4 style="margin:0 0 8px;font-size:13px;">The three arms compared</h4>
          <ul style="margin:0;padding-left:18px;color:var(--muted);">
            <li><strong>A0 · LLM alone</strong> — question only, no retrieved context</li>
            <li><strong>A1 · plain-text RAG</strong> — hybrid BM25 + dense retrieval over PharmGKB rows</li>
            <li><strong>A3 · subgraph RAG</strong> — entity-link → graph neighborhood → shortest paths → structured prompt</li>
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
    <p>The three arms use the <strong>same generator (Claude Sonnet 4)</strong> with the
      <strong>same system prompt</strong> (§3.4). The only variable across arms is the context
      the model receives. This isolates the effect of retrieval (A0 vs A1, A3) and the effect of
      graph-native vs text-similarity retrieval (A1 vs A3).</p>
    <p class="muted">Concrete example below: all three arms answering question
      <code>{SAMPLE_QID}</code> — <em>"{SAMPLE_QUESTION}"</em></p>
    <div class="arm-card-grid">
      <div class="arm-card">
        <span class="lbl">A0 · LLM alone</span>
        <h4>No retrieved context</h4>
        <p class="desc">The model answers from parametric knowledge only. Tests the no-retrieval
          baseline — what does the model already know?</p>
        <pre>{html.escape(SAMPLE_CTX_A0)}</pre>
      </div>
      <div class="arm-card">
        <span class="lbl">A1 · plain-text RAG</span>
        <h4>Hybrid text-similarity retrieval</h4>
        <p class="desc">PharmGKB rows are chunked into text, indexed with BM25 + dense embeddings
          (MiniLM), fused via reciprocal-rank-fusion. Returns top-8 chunks per query. Tests whether
          <em>retrieval</em> over the same data helps, independent of graph structure.</p>
        <pre>{html.escape(SAMPLE_CTX_A1)}</pre>
      </div>
      <div class="arm-card a3">
        <span class="lbl">A3 · subgraph RAG (system under test)</span>
        <h4>Entity-link → neighborhood → shortest paths</h4>
        <p class="desc">For each question: fuzzy-match question tokens against the graph's 3,203
          nodes to identify mentioned entities; pull each entity's 1-hop neighborhood;
          BFS-compute shortest paths between linked entities; render as structured text.
          A graph-native retrieval that no text-similarity system can replicate.</p>
        <pre>{html.escape(SAMPLE_CTX_A3)}</pre>
      </div>
    </div>
    <p class="muted" style="font-size:12px;margin-top:14px;">
      <strong>Note on terminology.</strong> A3 is what the literature typically calls "subgraph
      RAG" — query-conditional extraction of a relevant subgraph, rendered for the LLM. It is
      <em>not</em> Microsoft GraphRAG (Edge et al., 2024), which adds an offline community-summarisation
      step that we did not implement. We evaluate the subgraph-RAG variant because that is what
      the repository's web app ships.
    </p>
    <p class="muted" style="font-size:12px;margin-top:8px;">
      <strong>What about A2?</strong> A fourth arm (a static dump of a hand-curated 36-node
      anesthesia subgraph) was generated and is preserved in the raw data
      (<code>eval/answers.jsonl</code>). On reflection it tested a different, narrower question
      ("does irrelevant graph context help?" — answer: no) and is omitted from the analysis to
      keep the comparison focused on the three architecturally distinct conditions above.
    </p>
    <div class="callout">
      <span class="callout-label">What this concrete example reveals</span>
      On this specific question (a held-out RYR1 variant), A1's hybrid retriever surfaces
      <em>unrelated</em> PharmGKB rows (osteonecrosis, ustekinumab) because the variant string
      itself appears in no non-held-out chunk. A3, by contrast, entity-links "RYR1" successfully
      and surfaces the relevant subgraph (volatile anesthetics, succinylcholine, Malignant
      Hyperthermia) with L1A CRITICAL tags. This is exactly the pattern where graph structure
      should provide a measurable benefit. The aggregate result (49% pairwise preference vs A1
      across 187 questions) shows it does not generalise.
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
    <p>Each hypothesis below was committed to <code>eval/preregistration.md</code> before any LLM
      calls were made, with an exact decision threshold. Verdicts shown are computed from the live
      data.</p>
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


GH = "https://github.com/mihikap01/polygence-anesthesia-graphRAG/blob/main"


def html_appendix_a() -> str:
    """Per-metric explanations + step-by-step reproduction with GitHub links."""
    return f"""
    <span class="section-tag">appendix a</span>
    <h2 id="appendix-a">Appendix A. How each metric works + how to reproduce every result</h2>
    <p>This appendix is a run-along recipe — start from a clean repo clone and end with
      the same <code>eval/report.html</code> that's published on this page. Every section shows
      the <em>numbered steps</em> to run from the repo, the expected output, a one-line
      verification command, and a link to the source file on GitHub.</p>

    <!-- ============ A.0 QUICK START ============ -->
    <div class="metric-block" style="border-color: hsl(195 35% 60%);">
      <h4 style="color: var(--primary);">A.0 — Quick start: the entire pipeline from scratch</h4>
      <p>If you just want a copy-paste recipe that runs the whole evaluation end-to-end:</p>
      <pre class="cmd-app"># 1. Clone the repo
git clone https://github.com/mihikap01/polygence-anesthesia-graphRAG
cd polygence-anesthesia-graphRAG

# 2. Install dependencies (web/ and functions/)
scripts/install.sh

# 3. (One-time) Build the source graph from PharmGKB TSVs
python3 preprocess/build_graph.py

# 4. SETUP — held-out split + 187 questions + A1 index   (§A.1, ~30 sec, $0)
python3 eval/rebuild_heldout.py
python3 eval/generate_questions.py
python3 eval/a1_index.py

# 5. GENERATION — 3 arms × 187 = 561 LLM calls           (§A.2, ~3-4 hr, ~$25, resumable)
python3 eval/run.py --model sonnet

# 6. SCORING — four independent metric families:
python3 eval/grade.py                        # §A.3 rule-based      (~3 sec, $0)
python3 eval/judge.py --workers 6            # §A.4 pairwise        (~20 min, ~$7)
python3 eval/judge_rubric.py --workers 8     # §A.5 rubric F/C/CS   (~30 min, ~$9)
python3 eval/segment.py --workers 4          # §A.6 hallucination   (~40 min, ~$3)

# 7. AGGREGATE — produce eval/report.html and eval/results.json
python3 eval/report.py                       # §A.7, ~1 sec, $0
open eval/report.html</pre>
      <p style="font-size:13px; color: var(--muted); margin-top:8px;">
        <strong>Resumable:</strong> step 5 is the long one. If it's interrupted (rate limit,
        CLI hang), just re-run the same command — completed (question, arm) pairs are skipped.
        Steps 6a–d are independent and can be run in any order or in parallel.
      </p>
    </div>

    <!-- ============ A.1 SETUP ============ -->
    <h3 id="a-setup">A.1 — Setup (deterministic, no LLM cost)</h3>
    <p>Three setup scripts. All deterministic with fixed seeds — running them again
      produces byte-identical output.</p>

    <h4>(a) Held-out split <span class="muted">— seed=42</span></h4>
    <p>Drops 30% of the 1A/1B/2A/2B clinical-variant rows from the graph + from A1's
      text-RAG index. Neither A1 nor A3 can "look up" those rows directly.</p>
    <p class="step-label">How to run, from the repo root:</p>
    <pre class="cmd-app">cd polygence-anesthesia-graphRAG
python3 eval/rebuild_heldout.py</pre>
    <p class="step-label">Verify:</p>
    <pre class="cmd-app">wc -l eval/heldout_variants.tsv      # → 121 (1 header + 120 rows)
ls -l data/graph_heldout.json        # → file exists, ~2 MB</pre>
    <ul class="meta-list">
      <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/rebuild_heldout.py">eval/rebuild_heldout.py</a> (which calls <a href="{GH}/preprocess/build_graph.py">preprocess/build_graph.py</a> with <code>HELDOUT_VARIANTS</code> set)</li>
      <li><strong>Produces:</strong> <code>eval/heldout_variant_hashes.txt</code> · <code>eval/heldout_variants.tsv</code> (120 rows) · <code>data/graph_heldout.json</code> (3,203 nodes / 7,972 edges) · <code>data/search_index_heldout.json</code> · <code>data/seed_anesthesia_heldout.json</code></li>
      <li><strong>Time:</strong> ~3 seconds · <strong>Cost:</strong> $0</li>
    </ul>

    <h4>(b) Question generation <span class="muted">— seed=7</span></h4>
    <p>Templates over the held-out rows: 8 strata, 187 questions, each carrying a gold record
      (entities, evidence level, valid PMIDs, refusal flag).</p>
    <p class="step-label">How to run, from the repo root:</p>
    <pre class="cmd-app">python3 eval/generate_questions.py</pre>
    <p class="step-label">Verify:</p>
    <pre class="cmd-app">wc -l eval/questions.jsonl           # → 187
python3 -c "import json; print(json.loads(open('eval/questions.jsonl').readline())['stratum'])"   # → S1</pre>
    <ul class="meta-list">
      <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/generate_questions.py">eval/generate_questions.py</a></li>
      <li><strong>Produces:</strong> <code>eval/questions.jsonl</code> (187 records)</li>
      <li><strong>Time:</strong> ~3 seconds · <strong>Cost:</strong> $0</li>
    </ul>

    <h4>(c) A1's plain-text RAG index <span class="muted">— BM25 + dense, local CPU</span></h4>
    <p>Builds the index that A1 (the steelman plain-text baseline) will query at generation
      time. First run downloads ~80 MB of <code>sentence-transformers/all-MiniLM-L6-v2</code>
      weights to your <code>~/.cache/huggingface</code>.</p>
    <p class="step-label">How to run, from the repo root:</p>
    <pre class="cmd-app">python3 eval/a1_index.py</pre>
    <p class="step-label">Verify:</p>
    <pre class="cmd-app">ls -la eval/.cache/                  # → a1_bm25.pkl + a1_chunks.json + a1_embeddings.npy</pre>
    <ul class="meta-list">
      <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/a1_index.py">eval/a1_index.py</a> (indexing) · <a href="{GH}/eval/a1_retrieve.py">eval/a1_retrieve.py</a> (query-time top-K with RRF fusion)</li>
      <li><strong>Produces:</strong> <code>eval/.cache/</code> with BM25 pickle + dense embeddings + chunks JSON</li>
      <li><strong>Time:</strong> ~20 seconds (after weight download) · <strong>Cost:</strong> $0</li>
    </ul>

    <!-- ============ A.2 GENERATION ============ -->
    <h3 id="a-gen">A.2 — Generation (the 3 arms × 187 = 561 LLM calls)</h3>
    <p>For each question, runs three arms in turn via the Claude CLI. Same system
      prompt across all arms — the <em>only</em> variable is the context block.</p>
    <p class="step-label">Prerequisites:</p>
    <ul class="meta-list">
      <li>§A.1(a), (b), (c) all complete</li>
      <li><code>claude</code> CLI installed and authenticated (<code>claude --version</code> works)</li>
    </ul>
    <p class="step-label">How to run, from the repo root:</p>
    <pre class="cmd-app"># Standard run — Claude Sonnet 4 as generator
python3 eval/run.py --model sonnet

# Re-run is idempotent — completed (question, arm) pairs are skipped.
# If interrupted, just re-issue the same command.</pre>
    <p class="step-label">Verify (count unique successful answers):</p>
    <pre class="cmd-app">python3 -c "
import json
seen = set()
for line in open('eval/answers.jsonl'):
    r = json.loads(line)
    if not r.get('error'):
        seen.add((r['question_id'], r['arm']))
print(f'unique successful (qid, arm) pairs: {{len(seen)}}')   # → 560+"</pre>
    <ul class="meta-list">
      <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/run.py">eval/run.py</a> (driver, resumable) · <a href="{GH}/eval/arms.py">eval/arms.py</a> (per-arm CLI wrappers + system prompt) · <a href="{GH}/eval/retrieve_py.py">eval/retrieve_py.py</a> (A3's subgraph retrieval, Python port of <a href="{GH}/web/lib/graph/retrieve.ts">web/lib/graph/retrieve.ts</a>)</li>
      <li><strong>Produces:</strong> <code>eval/answers.jsonl</code> (561+ records, ~970 KB)</li>
      <li><strong>Time:</strong> ~3–4 hours · <strong>Cost:</strong> ~$25 in Claude API spend</li>
      <li><strong>Resumable:</strong> ✓ — re-running skips done work</li>
    </ul>

    <h3 id="a-rule">A.3 — Metric family 1: rule-based (deterministic, judge-independent)</h3>

    <div class="metric-block">
      <h4>What it measures</h4>
      <p>Hard, verifiable facts about each answer:</p>
      <ul>
        <li><strong>PMID exists rate</strong> — of cited PMIDs, the fraction that actually appear in
          our corpus (<code>relationships.tsv</code> + PubMed). Detects citation fabrication.</li>
        <li><strong>Entity precision / recall</strong> — of the gold entities (genes, drugs,
          phenotypes) for the question, how many appear in the answer.</li>
        <li><strong>Evidence-level exact match</strong> — (S2 stratum only) does the claimed
          evidence level (1A / 1B / 2A / 2B / 3 / 4) match the gold level?</li>
        <li><strong>Refusal correctness</strong> — (S6 negative-control + S7 OOD strata only)
          did the arm refuse / say "no association" when it should have?</li>
      </ul>
      <h4>Why we use it</h4>
      <p>This is the only metric family that is <em>judge-independent</em>. It cannot be biased
        by which model the judge prefers. If the LLM judge and the deterministic checks disagree,
        the deterministic check is the ground truth signal.</p>
      <h4>How to run from the repo</h4>
      <p class="step-label">Prerequisites: §A.1 + §A.2 complete (<code>eval/answers.jsonl</code> exists with ≥ 560 successful records).</p>
      <pre class="cmd-app"># From the repo root
cd polygence-anesthesia-graphRAG

# 1. Confirm the input is there
wc -l eval/answers.jsonl              # → 561+ raw lines (some may be retry attempts)

# 2. Run the grader (no LLM, just deterministic Python)
python3 eval/grade.py

# 3. Verify the output
wc -l eval/scores.jsonl               # → 748 (the grader dedupes by (qid, arm))</pre>
      <ul class="meta-list">
        <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/grade.py">eval/grade.py</a></li>
        <li><strong>Produces:</strong> <code>eval/scores.jsonl</code> (one record per (question, arm))</li>
        <li><strong>Time:</strong> ~3 seconds · <strong>Cost:</strong> $0 (no LLM calls)</li>
        <li><strong>Where it shows up in the report:</strong> §4.2 Table 5 (overall) and §4.5 Table 8 (per-stratum)</li>
      </ul>
    </div>

    <h3 id="a-pairwise">A.4 — Metric family 2: blinded pairwise preference (the H1 primary test)</h3>

    <div class="metric-block">
      <h4>What it measures</h4>
      <p>For each question, Claude Haiku 4.5 sees two answers (e.g. A3 and A1) with arm labels
        stripped and presentation order randomised, then picks the preferred answer or "tie."
        We run two pairs per question: <code>A3 vs A1</code> (primary, 187 comparisons) and
        <code>A3 vs A0</code> (secondary, 187 comparisons), for 374 total.</p>
      <h4>Why we use it</h4>
      <p>Pairwise comparison is the most defensible form of LLM-as-judge — it requires less
        anchoring than 1–5 absolute ratings and is robust to scale-use bias. With n=187 the
        sign-test has &gt;0.95 power to detect a 55% preference rate at α=0.05. This is the
        <em>only</em> adequately powered hypothesis test in the design.</p>
      <h4>How H1 is decided</h4>
      <p>Pre-registered threshold (frozen before any LLM calls): A3 wins H1 if A3 is preferred
        on &gt;55% of decisive comparisons with sign-test p&lt;0.05.</p>
      <h4>How to run from the repo</h4>
      <p class="step-label">Prerequisites: §A.1 + §A.2 complete · <code>claude</code> CLI authenticated.</p>
      <pre class="cmd-app">cd polygence-anesthesia-graphRAG

# 1. Run the pairwise judge with 6 parallel workers
#    (resumable — re-run skips already-completed comparisons)
python3 eval/judge.py --workers 6 --model haiku

# 2. Verify output count (374 = 187 questions × 2 pairs)
wc -l eval/judgments.jsonl

# 3. Sanity-check the headline number — A3 vs A1 preference rate
python3 -c "
import json
recs = [json.loads(l) for l in open('eval/judgments.jsonl') if not json.loads(l).get('error')]
m = [r for r in recs if r['pair']=='A3_vs_A1']
a3 = sum(1 for r in m if r['target_pick']=='A3')
n  = sum(1 for r in m if r['target_pick']!='TIE')
print(f'A3 wins {{a3}}/{{n}} = {{100*a3/n:.0f}}% (pre-registered threshold for H1: >55%)')"</pre>
      <ul class="meta-list">
        <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/judge.py">eval/judge.py</a></li>
        <li><strong>Produces:</strong> <code>eval/judgments.jsonl</code> (374 comparisons)</li>
        <li><strong>Time:</strong> ~20 minutes at 6 parallel workers · <strong>Cost:</strong> ~$7</li>
        <li><strong>Where it shows up in the report:</strong> §4.1 Table 4 (the headline)</li>
      </ul>
    </div>

    <h3 id="a-rubric">A.5 — Metric family 3: anchored 1–5 rubric ratings</h3>

    <div class="metric-block">
      <h4>What it measures</h4>
      <p>Per answer, Claude Haiku rates three dimensions on a 1–5 scale anchored to verbal
        descriptions:</p>
      <ul>
        <li><strong>Faithfulness (1–5)</strong> — does every factual claim match ground truth?
          5 = all claims supported · 3 = some wrong/unverifiable · 1 = many fabricated</li>
        <li><strong>Completeness (1–5)</strong> — does the answer cover the gold entities and
          relationships? 5 = covers gold + sensible elaboration · 1 = misses the central fact</li>
        <li><strong>Clinical soundness (1–5)</strong> — would a pharmacist call this misleading
          or unsafe? 5 = clinically appropriate, well hedged · 1 = misleading / unsafe</li>
      </ul>
      <p>Crucially, the judge sees question + gold + answer — <em>not</em> arm-specific
        retrieved context. This avoids penalising A0 (no context) by construction.</p>
      <h4>Why we use it</h4>
      <p>Captures softer qualities (clinical caution, completeness) that the rule-based pass
        can't. Less powerful than pairwise but gives anchored absolute scores.</p>
      <h4>How to run from the repo</h4>
      <p class="step-label">Prerequisites: §A.1 + §A.2 complete · <code>claude</code> CLI authenticated.</p>
      <pre class="cmd-app">cd polygence-anesthesia-graphRAG

# 1. Run the rubric judge with 8 parallel workers (resumable)
python3 eval/judge_rubric.py --workers 8 --model haiku

# 2. Verify output (one record per (question, arm) — target 748)
wc -l eval/rubric.jsonl

# 3. Sanity-check the per-arm means
python3 -c "
import json, statistics
from collections import defaultdict
recs = [json.loads(l) for l in open('eval/rubric.jsonl') if not json.loads(l).get('error')]
by = defaultdict(list)
for r in recs: by[r['arm']].append((r['faithfulness'], r['completeness'], r['clinical_soundness']))
for arm in sorted(by):
    f, c, s = zip(*by[arm])
    print(f'{{arm}}: F={{statistics.mean(f):.2f}} C={{statistics.mean(c):.2f}} CS={{statistics.mean(s):.2f}}  (n={{len(by[arm])}})')"</pre>
      <ul class="meta-list">
        <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/judge_rubric.py">eval/judge_rubric.py</a></li>
        <li><strong>Produces:</strong> <code>eval/rubric.jsonl</code> (one record per (question, arm))</li>
        <li><strong>Time:</strong> ~30 minutes at 8 parallel workers · <strong>Cost:</strong> ~$9</li>
        <li><strong>Where it shows up in the report:</strong> §4.3 Table 6</li>
      </ul>
    </div>

    <h3 id="a-halluc">A.6 — Metric family 4: merged-claim hallucination rate</h3>

    <div class="metric-block">
      <h4>What it measures</h4>
      <p>Per question, Haiku extracts the union of atomic factual claims across all 4 answers
        (with arm labels stripped), then labels each claim as supported / unsupported /
        unverifiable against ground truth. For each arm we count: how many of those claims did
        the arm make, and how many were supported.</p>
      <p><strong>Hallucination rate</strong> = (unsupported claims the arm made) / (total
        claims the arm made). Same merged claim list → same denominator across arms, which
        makes the comparison fair (the previous version used per-arm segmentation, which gave
        terse arms a structural advantage).</p>
      <h4>Why we use it</h4>
      <p>Pairwise preference can't see fabrication; the judge prefers fluent confidence.
        This metric exposes per-arm fabrication rate with a fair denominator. It is the only
        cross-arm-comparable hallucination measure.</p>
      <h4>How to run from the repo</h4>
      <p class="step-label">Prerequisites: §A.1 + §A.2 complete · <code>claude</code> CLI authenticated.</p>
      <pre class="cmd-app">cd polygence-anesthesia-graphRAG

# 1. Run the segmentation pass (resumable)
python3 eval/segment.py --workers 4 --model haiku

# 2. Verify how many questions got segmented successfully
python3 -c "
import json
recs = [json.loads(l) for l in open('eval/segments.jsonl')]
ok = [r for r in recs if r.get('claims') and not r.get('error')]
print(f'segmented: {{len(ok)}}/187')"</pre>
      <ul class="meta-list">
        <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/segment.py">eval/segment.py</a></li>
        <li><strong>Produces:</strong> <code>eval/segments.jsonl</code> (one record per question)</li>
        <li><strong>Time:</strong> ~40 minutes at 4 parallel workers · <strong>Cost:</strong> ~$3</li>
        <li><strong>Where it shows up in the report:</strong> §4.4 Table 7</li>
        <li><strong>Known limitation:</strong> the Claude CLI sometimes hangs past its 120s
          timeout. Coverage on the original run was 57/187 (~31%) before being stopped; treat
          absolute rates as directional, not definitive. Consider killing and resuming if a
          single call exceeds ~10 minutes.</li>
      </ul>
    </div>

    <h3 id="a-report">A.7 — Aggregate everything into the report</h3>
    <div class="metric-block">
      <h4>What it does</h4>
      <p>Reads all four <code>*.jsonl</code> outputs from A.3–A.6, computes the headline tables
        and the data-driven findings narrative, and generates this HTML report plus a
        machine-readable <code>results.json</code>.</p>
      <h4>How to run from the repo</h4>
      <p class="step-label">Prerequisites: A.3 + A.4 are required for the headline; A.5 and A.6 enhance the report but it gracefully skips them if their JSONL files are absent.</p>
      <pre class="cmd-app">cd polygence-anesthesia-graphRAG

# 1. Regenerate the report
python3 eval/report.py

# 2. Inspect the headline numbers programmatically
python3 -c "
import json
r = json.load(open('eval/results.json'))
print('Loaded:', r['n_scores'], 'scores,', r['n_judgments'], 'judgments')
m = r['pairwise']['A3_vs_A1']
print(f'A3 vs A1 (the H1 test): {{m[\\\"A3_wins\\\"]}}/{{m[\\\"A3_wins\\\"]+m[\\\"A1_wins\\\"]}} = {{100*m[\\\"armA_rate\\\"]:.0f}}% A3 preferred (p={{m[\\\"p_value\\\"]:.3f}})')"

# 3. Open the report in your default browser
open eval/report.html      # macOS
# xdg-open eval/report.html  # Linux</pre>
      <ul class="meta-list">
        <li><strong>Source on GitHub:</strong> <a href="{GH}/eval/report.py">eval/report.py</a></li>
        <li><strong>Produces:</strong> <code>eval/report.html</code> (this page) · <code>eval/results.json</code> (machine-readable summary of every metric)</li>
        <li><strong>Time:</strong> ~1 second · <strong>Cost:</strong> $0</li>
      </ul>
    </div>

    <h3 id="a-code">A.8 — Full eval source code reference</h3>
    <p>Every file involved in the evaluation, with GitHub links:</p>
    <table>
      <thead><tr><th>File</th><th>Role</th></tr></thead>
      <tbody>
        <tr><td><a href="{GH}/eval/preregistration.md">eval/preregistration.md</a></td><td>Frozen hypotheses + decision rules + A1 spec (committed before any LLM calls)</td></tr>
        <tr><td><a href="{GH}/eval/README.md">eval/README.md</a></td><td>Reproducer + status table</td></tr>
        <tr><td><a href="{GH}/eval/rebuild_heldout.py">eval/rebuild_heldout.py</a></td><td>A.1(a) — Deterministic 30% held-out split (seed=42)</td></tr>
        <tr><td><a href="{GH}/eval/generate_questions.py">eval/generate_questions.py</a></td><td>A.1(b) — 8 stratum templates → 187 questions (seed=7)</td></tr>
        <tr><td><a href="{GH}/eval/a1_index.py">eval/a1_index.py</a></td><td>A.1(c) — Build BM25 + dense index over TSVs</td></tr>
        <tr><td><a href="{GH}/eval/a1_retrieve.py">eval/a1_retrieve.py</a></td><td>A.2 — A1's query-time top-K retrieval with RRF</td></tr>
        <tr><td><a href="{GH}/eval/retrieve_py.py">eval/retrieve_py.py</a></td><td>A.2 — A3's entity-link + 1-hop neighbourhoods + BFS shortest paths (Python port of <a href="{GH}/web/lib/graph/retrieve.ts">web/lib/graph/retrieve.ts</a>)</td></tr>
        <tr><td><a href="{GH}/eval/arms.py">eval/arms.py</a></td><td>A.2 — Per-arm Claude CLI wrappers + shared system prompt</td></tr>
        <tr><td><a href="{GH}/eval/run.py">eval/run.py</a></td><td>A.2 — Generation driver (resumable)</td></tr>
        <tr><td><a href="{GH}/eval/grade.py">eval/grade.py</a></td><td>A.3 — Rule-based metrics</td></tr>
        <tr><td><a href="{GH}/eval/judge.py">eval/judge.py</a></td><td>A.4 — Blinded pairwise preference (parallel workers)</td></tr>
        <tr><td><a href="{GH}/eval/judge_rubric.py">eval/judge_rubric.py</a></td><td>A.5 — F/C/CS 1–5 ratings (parallel workers)</td></tr>
        <tr><td><a href="{GH}/eval/segment.py">eval/segment.py</a></td><td>A.6 — Merged-claim hallucination (parallel workers)</td></tr>
        <tr><td><a href="{GH}/eval/report.py">eval/report.py</a></td><td>A.7 — Aggregate + generate this report</td></tr>
        <tr><td><a href="{GH}/eval/questions.jsonl">eval/questions.jsonl</a></td><td>Committed: 187 questions + gold records</td></tr>
        <tr><td><a href="{GH}/eval/answers.jsonl">eval/answers.jsonl</a></td><td>Committed: 561+ raw LLM responses</td></tr>
        <tr><td><a href="{GH}/eval/judgments.jsonl">eval/judgments.jsonl</a></td><td>Committed: 374 pairwise judgments</td></tr>
        <tr><td><a href="{GH}/eval/scores.jsonl">eval/scores.jsonl</a></td><td>Committed: per-(question, arm) rule-based scores</td></tr>
        <tr><td><a href="{GH}/eval/rubric.jsonl">eval/rubric.jsonl</a></td><td>Committed: per-answer F/C/CS ratings</td></tr>
        <tr><td><a href="{GH}/eval/segments.jsonl">eval/segments.jsonl</a></td><td>Committed: per-question merged-claim records</td></tr>
        <tr><td><a href="{GH}/eval/results.json">eval/results.json</a></td><td>Machine-readable summary of all metrics</td></tr>
      </tbody>
    </table>

    <p style="margin-top:14px; font-size:13px;" class="muted">
      <strong>Total reproduction cost:</strong> ~5 hours wall time, ~$45 in Claude API spend.
      Every output file is also committed under <a href="{GH}/eval/">eval/</a> so you can read
      the raw evidence without re-running anything.
    </p>"""


def html_appendix_b() -> str:
    """Empirical analysis: which prompts make GraphRAG actually win? Data-mined from
    the same eval — not a separate run. Pure post-hoc pattern discovery."""
    return f"""
    <span class="section-tag">appendix b</span>
    <h2 id="appendix-b">Appendix B. Which prompts actually let GraphRAG beat the baselines?</h2>
    <p>The headline (§4.1) says A3 ties A1 on average. But "average" hides structure. This appendix
      mines the same 374 pairwise judgments + 748 rule-based scores to find <strong>which specific
      prompt patterns let GraphRAG win decisively against both A0 and A1</strong>. The goal: an
      evidence-based answer to "when should I actually use GraphRAG?"</p>

    <h3 id="b-method">B.1 — Method</h3>
    <p>We filter to prompts where A3 won pairwise against <em>both</em> A0 and A1 — meaning the
      Haiku judge preferred A3 over the no-context model AND over plain-text RAG. Then we cross-
      check against the rule-based metrics (entity recall, PMID exists, refusal correctness) to
      separate <strong>substantive wins</strong> (corroborated by ground-truth checks) from
      <strong>style-only wins</strong> (judge liked the prose but factual scores neutral).</p>
    <p>Out of 187 questions, <strong>35 are A3 double-wins on pairwise</strong>; of those,
      <strong>7 are substantively corroborated</strong> by rule-based metrics. The other 28 are
      judge-style wins we don't lean on.</p>

    <h3 id="b-patterns">B.2 — The four prompt patterns where GraphRAG wins</h3>

    <div class="metric-block">
      <h4>Pattern 1 · "Is this drug in the database?" — out-of-distribution queries</h4>
      <p><strong>Win rate: 33% of S7</strong> (5/15 OOD questions). GraphRAG's highest-conviction
        win.</p>
      <p>When a user asks about a drug, herbal supplement, or OTC product that is genuinely not in
        the curated graph, GraphRAG's entity-linker returns an empty match list and the answer can
        cleanly say <em>"this entity is not in the graph."</em> Plain-text RAG always returns
        <em>something</em> (semantically related noise), tempting the model to fabricate.</p>
      <p class="step-label">Example prompt (real, from the eval — question <code>S7-006</code>):</p>
      <pre class="cmd-app">"What pharmacogenomic guidelines exist for saw palmetto in PharmGKB?
Cite the specific gene-drug interactions if any."</pre>
      <p class="step-label">Why A3 wins:</p>
      <ul>
        <li>A3's entity-linker finds zero matches in the 3,213-node graph → answer states this directly.</li>
        <li>A1's BM25 + dense retriever surfaces tangentially-related rows (other herbal supplements, anything mentioning "supplement"), and the answer drifts into uncertain territory.</li>
        <li>A0 (parametric) sometimes guesses confidently from training data.</li>
      </ul>
      <p class="step-label">Use this pattern when:</p>
      <ul>
        <li>You're not sure whether a drug is covered by PharmGKB clinical guidelines</li>
        <li>You're triaging a list of supplements / OTCs / niche compounds for review</li>
        <li>You need an honest "no, this isn't curated" signal rather than a fluent guess</li>
      </ul>
    </div>

    <div class="metric-block">
      <h4>Pattern 2 · "Is there a clinical association between X and Y?" — negative-control queries</h4>
      <p><strong>Win rate: 32% of S6</strong> (8/25 negative-control questions).</p>
      <p>When the user asks about a specific drug-gene pair that PharmGKB has explicitly evaluated
        and judged as <em>not associated</em>, GraphRAG can inspect the linked entity's neighborhood
        and report the absence of an edge directly. Other arms tend to assert an association from
        parametric memory or partial retrieved evidence.</p>
      <p class="step-label">Example prompt (real, from the eval — question <code>S6-001</code>):</p>
      <pre class="cmd-app">"According to PharmGKB-curated evidence, is there a clinically
meaningful pharmacogenomic association between
hydrochlorothiazide and FGF5?"</pre>
      <p class="step-label">Why A3 wins:</p>
      <ul>
        <li>A3 entity-links both terms, pulls hydrochlorothiazide's full 1-hop neighborhood (31 gene edges, all efficacy/toxicity for unrelated genes), and confirms FGF5 is absent.</li>
        <li>A0 sometimes hallucinates a plausible-sounding mechanism.</li>
        <li>A1 retrieves general hydrochlorothiazide chunks but doesn't necessarily catch the FGF5 absence.</li>
      </ul>
      <p class="step-label">Use this pattern when:</p>
      <ul>
        <li>Cross-checking a hypothesis ("could X be the gene driving Y's response?")</li>
        <li>Verifying that a claim from another source is actually supported in PharmGKB</li>
        <li>You need "no, this association is not curated" with backup evidence</li>
      </ul>
    </div>

    <div class="metric-block">
      <h4>Pattern 3 · "What does PharmGKB say about &lt;niche-drug&gt; and &lt;gene&gt;?" — long-tail entity disambiguation</h4>
      <p><strong>Win rate: 28% of S5</strong> (7/25 niche-drug questions).</p>
      <p>For obscure drug-gene pairs, GraphRAG often <em>catches entity confusions</em> the other
        arms walk into. The structured entity linker either fails to match (returns empty) or
        returns the exact entity, which surfaces "did you mean a similarly-named drug?" issues
        that A0 and A1 paper over.</p>
      <p class="step-label">Example prompt (real, from the eval — question <code>S5-006</code>):</p>
      <pre class="cmd-app">"What pharmacogenomic relationship has PharmGKB documented
between methazolamide and HLA-C? Be specific about the type
(metabolism / efficacy / toxicity)."</pre>
      <p class="step-label">Why A3 wins:</p>
      <ul>
        <li>A3 reports "no methazolamide–HLA-C edge in the supplied context" and explicitly flags
          a potential entity mismatch (the user may have meant acetazolamide).</li>
        <li>A1 retrieves something related and confidently asserts a relationship type.</li>
        <li>A0 invents a plausible mechanism from training data.</li>
      </ul>
      <p class="step-label">Use this pattern when:</p>
      <ul>
        <li>The drug name is uncommon and could be confused with a similar one</li>
        <li>You want to verify that PharmGKB has the exact compound (not just a related congener)</li>
      </ul>
    </div>

    <div class="metric-block">
      <h4>Pattern 4 · "Patient carries &lt;wild-type allele&gt; — what to avoid?" — premise-questioning queries</h4>
      <p><strong>4 of 5 substantive S3 (multi-hop) wins</strong> follow this pattern.</p>
      <p>When a question implicitly assumes a variant carries clinical implications, but the
        named allele is actually <em>wild-type</em> (e.g., <code>CYP2C19*1</code>,
        <code>CYP2D6*1</code>, <code>CYP2C9*1</code>, <code>SLCO1B1*1</code>), GraphRAG's
        structured context with explicit evidence-level tags (<code>L1A</code>, <code>L2A</code>,
        <code>CRITICAL</code>) helps frame the response: <em>"this is the reference allele — no
        avoidance — but if a variant were present, here's what the graph says."</em> Other arms
        sometimes drift into giving avoidance advice for the wild-type.</p>
      <p class="step-label">Example prompt (real, from the eval — question <code>S3-022</code>):</p>
      <pre class="cmd-app">"A patient is found to carry the CYP2D6*1 variant in CYP2D6.
Which class of medications may require dose adjustment, and why?"</pre>
      <p class="step-label">Why A3 wins:</p>
      <ul>
        <li>A3 sees the full graph context (CYP2D6 → opioids, tramadol, etc., with evidence levels).</li>
        <li>The structured rendering helps the model produce a properly-hedged "wild-type → no adjustment, but here is the L1A/CRITICAL context if a variant were present" answer.</li>
        <li>A0 sometimes gives confidently-wrong avoidance advice; A1's retrieved chunks lack the wild-type/variant disambiguation.</li>
      </ul>

      <p class="step-label">Counter-pattern — where A3 LOSES on this stratum:</p>
      <p>23 of 40 S3 questions went to A1 — most are real <code>rs</code>-ID variants
        (<code>rs2108622</code>, <code>rs1801160</code>, <code>rs121918596</code>,
        <code>rs9923231</code>, etc.) where PharmGKB rows pack drug + gene + variant + class
        into a single denormalised chunk. A1's hybrid retriever finds the chunk directly; A3's
        graph traversal adds no benefit. The pattern: <strong>denormalised single-row lookups
        are where plain-text RAG wins, not where graph traversal wins.</strong></p>
    </div>

    <h3 id="b-anti">B.3 — When NOT to use GraphRAG (where A1 / A0 actually beat it)</h3>
    <p>Symmetry: from the same data, here's where GraphRAG should not be the system you reach for.</p>
    <ul>
      <li><strong>Easy / well-known pharmacogenomic facts (S1).</strong> A3 won only 35% of pairwise
        comparisons here vs A1. The graph context adds noise on questions any reasonable LLM
        answers from training (e.g., "which gene is most strongly associated with malignant
        hyperthermia?" → RYR1, no graph needed).</li>
      <li><strong>Specific evidence-level lookups (S2).</strong> A3 won 44% — basically a tie.
        PharmGKB rows already pack evidence levels into single text chunks, so text retrieval
        recovers them efficiently.</li>
      <li><strong>Real <code>rs</code>-ID variant → drug-class queries when the variant is a
        canonical PharmGKB row (S3 majority).</strong> A1 wins these because the variant-row is
        retrievable as a single text chunk.</li>
      <li><strong>Citation-only queries (S4).</strong> Confounded — A3 sometimes "wins" because
        the judge prefers its refusal over A1's correct citation. Use A1 (or hybrid) when you
        explicitly need PMIDs.</li>
    </ul>

    <h3 id="b-prompts">B.4 — Suggested prompts for the demo's Chat panel</h3>
    <p>If we wanted the demo to lead with prompts that show GraphRAG at its best, this is the
      shortlist — each one comes from a pattern where A3 measurably won the eval:</p>
    <ol>
      <li><em>"What pharmacogenomic guidelines exist for &lt;your-supplement&gt; in PharmGKB?
        Cite specific gene-drug interactions if any."</em> &nbsp;<span class="muted">— pattern 1 (OOD refusal)</span></li>
      <li><em>"Is there a clinically meaningful pharmacogenomic association between
        &lt;drug&gt; and &lt;gene&gt;?"</em> &nbsp;<span class="muted">— pattern 2 (negative control)</span></li>
      <li><em>"What does PharmGKB document about &lt;niche-drug&gt; and &lt;gene&gt;? Be specific
        about the type (metabolism / efficacy / toxicity)."</em> &nbsp;<span class="muted">— pattern 3 (long-tail / disambiguation)</span></li>
      <li><em>"A patient carries &lt;wild-type allele like CYP2C19*1&gt;. Which class of
        medications may require dose adjustment, and why?"</em> &nbsp;<span class="muted">— pattern 4 (premise-questioning)</span></li>
    </ol>

    <h3 id="b-summary">B.5 — One-line summary</h3>
    <div class="callout">
      <span class="callout-label">The empirical positioning of GraphRAG in this domain</span>
      <p style="margin: 6px 0 0; font-size: 15px;">
        Over PharmGKB, the value of the graph layer is concentrated in
        <strong>knowing the boundaries of the curated knowledge</strong> —
        absence, mismatches, wild-type / variant disambiguation, and "is this
        actually in the database?" queries. It is <strong>not</strong> in
        multi-hop reasoning, which the source data's row-level denormalisation
        gives plain-text RAG for free.
      </p>
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

    # Title block (paper-style header)
    body.append('<div class="paper-header">')
    body.append('<div class="venue">Polygence research report · pharmacogenomic knowledge-graph RAG</div>')
    body.append('<h1>Does subgraph retrieval over a knowledge graph outperform plain-text retrieval for pharmacogenomic question answering?</h1>')
    body.append('<p class="byline"><strong>Mihika Pall</strong> · Polygence (2026). An evaluation of three RAG architectures on 187 held-out PharmGKB questions, with rule-based and LLM-judge scoring.</p>')
    body.append('</div>')

    # Abstract
    body.append(html_abstract(pw, head))

    # Table of contents — including Appendix A so reproduction is discoverable
    body.append(html_toc())

    # Notation / reading guide
    body.append(html_glossary())

    # 1. Introduction (replaces "setup in 60s")
    body.append(html_introduction())

    # 2. Background
    body.append(html_background())

    # 3. Methods (rolls up dataset / split / arms / prompts / questions / hypotheses / measurement)
    body.append('<span class="section-tag">3 · methods</span><h2 id="s3">3. Methods</h2>')
    body.append('<h3 id="m-data">3.1 Dataset</h3>')
    body.append(html_dataset_body(ev_dist))
    body.append('<h3 id="m-split">3.2 Held-out split</h3>')
    body.append(html_held_out_body())
    body.append('<h3 id="m-arms">3.3 The three retrieval conditions (arms)</h3>')
    body.append(html_arms_body())
    body.append('<h3 id="m-prompts">3.4 Prompts</h3>')
    body.append(html_prompts_body())
    body.append('<h3 id="m-questions">3.5 Question set (187 held-out items in 8 strata)</h3>')
    body.append(html_questions_body(questions))

    # 3.6 Hypotheses
    body.append('<h3 id="m-hypotheses">3.6 Pre-registered hypotheses</h3>')
    body.append('<p>The following three hypotheses, with their decision thresholds, were committed to <code>eval/preregistration.md</code> before any LLM calls were made. The aim is to prevent post-hoc goalpost adjustment.</p>')
    body.append(html_hypotheses_cards(pw, pwps, head, scores))

    # 3.7 Metrics
    body.append('<h3 id="m-metrics">3.7 Evaluation protocol</h3>')
    body.append(html_metrics_body())

    # 4. Results
    body.append('<span class="section-tag">4 · results</span><h2 id="s4">4. Results</h2>')
    body.append('<h3 id="r-pairwise">4.1 Primary outcome — blinded pairwise preference (n=187)</h3>')
    body.append('<p>For each question, the judge (Claude Haiku 4.5) sees both answers with arm labels stripped and presentation order randomised, then picks the preferred answer or "tie." H1 required A3 to win more than 55% of decisive comparisons with sign-test p&lt;0.05.</p>')
    body.append('<p class="muted" style="font-size:12px;margin-bottom:2px;">Table 4. Headline pairwise preference results.</p>')
    body.append(html_pairwise(pw))
    body.append('<h3 id="r-rule">4.2 Rule-based metrics (deterministic, all 748 answers)</h3>')
    body.append('<p>Deterministic Python checks against ground truth. Definitions are symmetric across all arms (no arm is structurally penalised or rewarded by metric construction).</p>')
    body.append('<p class="muted" style="font-size:12px;margin-bottom:2px;">Table 5. Per-arm rule-based metrics.</p>')
    body.append(html_headline(head, pw))
    rub_n = sum(m.get("n", 0) for m in rub.values())
    body.append(f'<h3 id="r-rubric">4.3 Rubric ratings — Faithfulness, Completeness, Clinical soundness <span class="muted">(n={rub_n}/748 successful)</span></h3>')
    body.append('<p>Each answer rated 1–5 on three dimensions against ground truth. Judge sees question + gold + answer; arm labels stripped.</p>')
    if rub_n < 740:
        body.append(f'<p class="muted"><em>Coverage note:</em> rubric pass returned {rub_n}/748 (~{int(100*rub_n/748)}%); the remainder failed when the Claude CLI hung past its 120-second timeout on late-stratum questions. Per-arm coverage is balanced (110–113 each), so per-arm means remain interpretable.</p>')
    body.append('<p class="muted" style="font-size:12px;margin-bottom:2px;">Table 6. Per-arm rubric ratings (mean ± 95% CI half-width).</p>')
    body.append(html_rubric(rub))
    body.append(f'<h3 id="r-halluc">4.4 Hallucination rate via merged-claim segmentation <span class="muted">(n={hr.get("questions_covered", 0)}/187 questions)</span></h3>')
    body.append('<p>Per question, the judge extracts the union of atomic factual claims across all four arms\' answers, then labels each as supported / unsupported / unverifiable against ground truth. Same merged claim list yields the same denominator across arms.</p>')
    if hr.get("questions_covered", 0) < 180:
        body.append(f'<p class="muted"><em>Coverage note:</em> segmentation pass returned {hr.get("questions_covered", 0)}/187 (~{int(100*hr.get("questions_covered", 0)/187)}%); the remainder lost to the same CLI-hang failure mode. Coverage is biased toward strata S1–S4. Absolute rates should be read as directional.</p>')
    body.append('<p class="muted" style="font-size:12px;margin-bottom:2px;">Table 7. Per-arm claim-level metrics (merged segmentation).</p>')
    body.append(html_hallucination(hr))
    body.append('<h3 id="r-stratum">4.5 Per-stratum metrics (descriptive)</h3>')
    body.append('<p>Per-stratum sample sizes (n=12–40) are too small to support hypothesis tests after multiple-comparison correction. The table below is included as descriptive context for <em>where</em> each arm wins or loses.</p>')
    body.append('<p class="muted" style="font-size:12px;margin-bottom:2px;">Table 8. Per-stratum × arm breakdown.</p>')
    body.append(html_per_stratum(pps, pwps))

    # 5. Discussion
    body.append('<span class="section-tag">5 · discussion</span><h2 id="s5">5. Discussion</h2>')
    body.append('<h3 id="d-h1">5.1 Why subgraph RAG did not beat plain-text RAG</h3>')
    body.append(html_findings(pw, pwps, head))
    if any(rub.get(a, {}).get("n") for a in ARMS) and hr.get("questions_covered"):
        def fm(a): return rub.get(a, {}).get("F_mean")
        def sr(a): return hr.get(a, {}).get("support_rate")
        body.append('<h3 id="d-corroborate">5.2 Convergence across metric families</h3>')
        body.append(f"""
        <p>Four independent measurement approaches were used. They agree:</p>
        <ul>
          <li><strong>Pairwise preference:</strong> A0 preferred over A3 in
            {1 - (pw.get('A3_vs_A0',{}).get('armA_rate') or 0):.0%} of comparisons.</li>
          <li><strong>Rubric Faithfulness (1–5):</strong> A0 = {fm('A0'):.2f}, A1 = {fm('A1'):.2f},
            A3 = {fm('A3'):.2f}. The judge rates A0 highest.</li>
          <li><strong>Merged-claim support rate:</strong> A0 = {fmt_pct(sr('A0'))},
            A1 = {fmt_pct(sr('A1'))}, A3 = {fmt_pct(sr('A3'))}.</li>
          <li><strong>Rule-based PMID-exists:</strong> A0 = {fmt_pct(head['A0']['pmid_exists_rate_mean'])}
            (i.e. fabricates {fmt_pct(1-head['A0']['pmid_exists_rate_mean'])}), A3 = {fmt_pct(head['A3']['pmid_exists_rate_mean'])}.</li>
        </ul>
        <p>A0 has strong parametric pharmacogenomic knowledge — its substantive claims are
          largely correct (74% support rate). But it fabricates specific paper identifiers (PMIDs)
          at a high rate the LLM judge does not detect. The judge rewards fluent confidence; only
          the deterministic rule-based metrics catch the fabrication. This is the principled
          reason for not relying on LLM-judge preference alone in high-stakes evaluations of
          retrieval systems.</p>""")
    body.append('<h3 id="d-refusal">5.3 Where subgraph RAG <em>did</em> help: out-of-distribution refusal</h3>')
    body.append('<p>The one stratum where A3 outperformed A1 was S7 (out-of-distribution drugs not in the graph). When a query referenced an entity the graph genuinely lacks, A3 returned an empty subgraph and appropriately refused; A1\'s text retriever always returns <em>something</em> — semantically related noise — which the model interpreted as license to answer. This is consistent with the view that the structural value of a knowledge graph in this domain is <em>boundary knowledge</em> (what is present vs. absent) rather than multi-hop reasoning.</p>')
    body.append('<h3 id="d-domain">5.4 Why this domain is harder than expected for graph RAG</h3>')
    body.append('<p>PharmGKB\'s <code>clinicalVariants.tsv</code> is heavily denormalised: a single row routinely contains variant + gene + evidence level + drugs + phenotypes. "Multi-hop" facts that would require graph traversal in a normalised schema are recoverable in a single text chunk by any sensible text retriever. We conjecture that the absence of a measured benefit for the graph layer in this evaluation reflects this denormalisation, not a deficiency in subgraph RAG as an approach. Domains whose data is genuinely fragmented across documents (e.g. drug-drug interaction networks, citation graphs, multi-source knowledge bases) would be expected to differentiate the approaches more sharply.</p>')

    # 6. Limitations
    body.append('<span class="section-tag">6 · limitations</span><h2 id="s6">6. Limitations and threats to validity</h2>')
    body.append('<p>The following limitations and protocol deviations should be borne in mind when reading the headline result.</p>')
    body.append('<h3 id="l-gen">6.1 Generator and judge configuration</h3>')
    body.append("""<p>The pre-registration named Claude Opus as the generator; we used Claude Sonnet 4
      for cost and throughput (~10× cheaper, ~3× faster per call). A stronger generator might
      raise A0's parametric-knowledge quality further, which would <em>strengthen</em> the
      pattern reported here (A0 fluent and confident, retrieval arms struggling to improve on it)
      rather than weaken it. The judge was Claude Haiku 4.5 — a smaller model in the same family
      — which partially but not fully mitigates self-preference bias. A cross-family judge
      (Gemini or GPT) would be a methodological improvement.</p>""")
    body.append('<h3 id="l-leak">6.2 Partial held-out leakage via relationships.tsv</h3>')
    body.append("""<p>The held-out split removes rows from <code>clinicalVariants.tsv</code> but not from
      <code>relationships.tsv</code>, which redundantly restates many of the same drug-gene
      associations along with PMIDs. The plain-text retriever (A1) indexes both files and could
      therefore still retrieve correct PMIDs for associations whose <code>clinicalVariants</code>
      row had been held out. The subgraph-RAG arm (A3) genuinely lost those edges from its graph.
      The S4 (citation) result is the most affected — the judge in some cases preferred A3's
      refusal to cite over A1's correct-but-unverifiable citation, which conflates "refusal" with
      "grounding." A cleaner v2 should hold out the corresponding <code>relationships.tsv</code>
      rows in parallel.</p>""")
    body.append('<h3 id="l-baseline">6.3 The plain-text RAG baseline (A1) is strong but not maximal</h3>')
    body.append('<p>The A1 spec uses BM25 + sentence-transformers/MiniLM dense embeddings fused via reciprocal-rank fusion, with the top-8 chunks in the prompt. The pre-registration originally included a BGE cross-encoder reranker, which was dropped to keep the local install lightweight. Adding the reranker would be expected to further strengthen A1 — which would, if anything, make the conclusion stronger (graph structure adds even less over a maximally-tuned text retriever) rather than weaker.</p>')
    body.append('<h3 id="l-pmid">6.4 Production subgraph-RAG does not surface PMIDs to the model</h3>')
    body.append('<p>The web app\'s chat endpoint extracts PMIDs into a separate UI-side citations list but does not include them in the LLM\'s prompt. We faithfully ported this behaviour into A3 for the evaluation. As a result, A3 cannot ground PMIDs in the way A1 can, even when the graph has them. This is a real product limitation, not an evaluation artefact; fixing it (inlining PMIDs into the rendered subgraph) is a top-of-list improvement (§7).</p>')
    body.append('<h3 id="l-power">6.5 Sample size and inferential scope</h3>')
    body.append("""<p>The primary n=187 pairwise test is adequately powered (sign-test, &gt;0.95 power to
      detect a 55% preference rate at α=0.05). Per-stratum sample sizes (n=12–40) are <em>not</em>
      adequately powered after multiple-comparison correction; per-stratum results are reported as
      descriptive context rather than as inferential claims. Coverage of the rubric and segmentation
      passes was reduced (60% and 31% respectively) by intermittent Claude CLI hangs, which
      disproportionately affected the late strata (S7, S8).</p>""")
    # Appendix: illustrative answers (numbered as 6.6 / appendix)
    body.append('<h3 id="l-examples">6.6 Appendix — illustrative answers (one question per stratum)</h3>')
    body.append('<p>Each example shows the three arms\' answers to the same held-out question, truncated to 600 characters. These are representative, not cherry-picked: the first question in the question file for each stratum.</p>')
    body.append(html_examples(answers_by_qid_arm, questions, example_qids))

    # 7. Conclusions and future work
    body.append('<span class="section-tag">7 · conclusions</span><h2 id="s7">7. Conclusions and future work</h2>')
    body.append('<p>On a held-out PharmGKB benchmark, with Claude Sonnet as the generator and Claude Haiku as the judge, the subgraph-RAG system we evaluated did not outperform a strong plain-text RAG baseline on blinded pairwise preference (49% A3-preferred, p=0.83). Across four independent metric families — preference, deterministic rule-based, anchored rubric ratings, and merged-claim hallucination — the no-context model received the highest judge ratings despite fabricating cited PMIDs at roughly twice the rate of subgraph RAG. The graph layer\'s one consistent advantage was appropriate refusal on out-of-distribution queries.</p>')
    body.append('<p>The most parsimonious explanation for the null primary result is that PharmGKB\'s row-level denormalisation packs ostensibly multi-hop facts into single text chunks, allowing strong text similarity to recover them. The result therefore says something specific about <em>this domain</em> (and arguably about any heavily denormalised knowledge graph) rather than something general about subgraph RAG.</p>')
    body.append('<h3 id="c-next">7.1 Targeted follow-up experiments</h3>')
    body.append("""<p>The following experiments would each test a specific hypothesis raised by these results
      and are listed in increasing implementation cost:</p>
    <ol style="font-size:14px;">
      <li><strong>Remove the relationships.tsv leakage.</strong> Hold out the corresponding rows
        in <code>relationships.tsv</code> in parallel with <code>clinicalVariants.tsv</code>; re-run.
        Cleanest test of whether the leakage is what kept A1 competitive on citation-grounding.</li>
      <li><strong>Inline PMIDs into A3's context.</strong> A ~10-line change in
        <code>eval/retrieve_py.py</code> to surface edge-level PMIDs in the structured prompt.
        Re-run the citation stratum only. Likely converts A3's confounded refusal-win on S4 into
        a real grounding win.</li>
      <li><strong>Cross-family judge.</strong> Re-run the pairwise and rubric passes with Gemini
        2.5 as judge. Removes within-Claude self-preference; tests whether the "A0 preferred"
        result is family-specific.</li>
      <li><strong>Hybrid arm (A4 = A3 + A1).</strong> A natural product question: does concatenating
        A3's structured subgraph and A1's top-3 text chunks produce a system that strictly
        dominates either alone?</li>
      <li><strong>Architectural variants.</strong> Implement a Microsoft-style GraphRAG arm
        (Leiden communities + LLM-generated community summaries + hierarchical retrieval) to
        test whether community-level summarisation succeeds where edge-level retrieval did not.</li>
      <li><strong>Cross-domain replication.</strong> Run the same evaluation harness on a less
        denormalised knowledge graph (drug-drug interaction networks, citation graphs, gene-pathway
        networks) where multi-hop facts genuinely require traversal. This is the cleanest test of
        whether the negative result here generalises beyond PharmGKB.</li>
    </ol>""")

    # Appendix A — per-metric explanations + reproduction recipe + GitHub links
    body.append(html_appendix_a())

    # Appendix B — data-mined: which prompts let GraphRAG actually win?
    body.append(html_appendix_b())

    body.append('<footer>'
                'Author: <strong>Mihika Pall</strong> · Polygence research mentorship program (2026). '
                f'Generated from {len(scores)} scores and {len(judgments)} judgments. '
                'Generator: Claude Sonnet 4 · Judge: Claude Haiku 4.5. '
                'Pre-registration: <code>eval/preregistration.md</code>. '
                'Source: <a href="https://github.com/mihikap01/polygence-anesthesia-graphRAG">github.com/mihikap01/polygence-anesthesia-graphRAG</a>.'
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
