#!/usr/bin/env python3
"""
Candidate runner (SERIAL — simpler + more debuggable than parallel).

Takes prompts from eval/candidates.jsonl, runs 3 arms per candidate via
Claude CLI, then runs 2 pairwise Haiku judges per candidate.

Output:
  eval/candidate_answers.jsonl
  eval/candidate_judgments.jsonl
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
CANDS = EVAL / "candidates.jsonl"
ANS = EVAL / "candidate_answers.jsonl"
JDG = EVAL / "candidate_judgments.jsonl"

CHAT_SYSTEM = """You are a GraphRAG assistant answering questions about a pharmacogenomic knowledge graph.

Rules:
- Ground every claim in the supplied context. If the context does not support an answer, say so plainly.
- Cite PMIDs in-line as [PMID:xxxxxx] when you have them.
- Prefer the shortest reasoning path (drug → gene/variant → phenotype).
- Highlight CRITICAL / level 1A or 1B evidence when relevant.
- Keep answers under 200 words unless asked for more depth.
- Do not fabricate dosing recommendations or clinical advice."""

A0_NOCTX = "(No retrieved context provided. Answer from your own knowledge if confident; otherwise say so.)"

JUDGE_SYSTEM = """You are an expert pharmacogenomics evaluator. You will see a question and two answers (A and B) from different systems. Pick which answer better serves a clinical/research user.

Criteria (in order of importance):
1. Factual correctness vs. PharmGKB clinical guidelines
2. Specificity (claims real evidence levels, names correct entities, cites real PMIDs)
3. Appropriate epistemic stance (refuses when uncertain, hedges where appropriate)
4. Clinical soundness (does not mislead a reader who would act on it)
5. Style is NOT a criterion — ignore differences in formatting

Reply with EXACTLY one of: "A" or "B" or "TIE", on its own line. Then a single sentence explaining why. No preamble."""


# ---------- Graph loading (full production graph) ----------

def load_graph():
    p_graph = ROOT / "data" / "graph.json"
    p_si = ROOT / "data" / "search_index.json"
    g = json.loads(p_graph.read_text())
    si = json.loads(p_si.read_text())
    node_by_id = {n["id"]: n for n in g["nodes"]}
    adj = defaultdict(list)
    for e in g["edges"]:
        adj[e["source"]].append(e)
        adj[e["target"]].append(e)
    return {"nodes": g["nodes"], "edges": g["edges"], "node_by_id": node_by_id, "adj": adj, "search_index": si}


# ---------- A3 retrieval (inline mini-port, avoid module cycles) ----------

STOPWORDS = {"a","an","the","is","are","was","were","be","of","to","for","in","on","at","by","with",
             "from","and","or","but","if","then","than","so","as","it","this","that","these","those",
             "what","which","who","whom","whose","where","when","why","how","does","do","did","can",
             "could","should","would","will","have","has","had","between","about","into","through",
             "pharmacogenomic","association","associations","reference","references","cite","citation",
             "clinical","evidence","guideline","guidelines","level","interaction","interactions",
             "carry","carries","carrying","patient","class","gene","drug","phenotype","variant"}


def ngrams(question):
    tokens = [t for t in re.split(r"\s+", re.sub(r"[^\w\s-]", " ", question.lower()))
              if len(t) >= 3 and t not in STOPWORDS]
    grams = []
    for n in (3, 2, 1):
        for i in range(0, len(tokens) - n + 1):
            grams.append(" ".join(tokens[i:i+n]))
    return list(dict.fromkeys(grams))


def link_entities(question, gd, max_entities=6):
    try:
        from rapidfuzz import fuzz, process as fuzzproc
    except ImportError:
        # exact substring fallback
        q = question.lower()
        seen = {}
        for e in gd["search_index"]:
            label = (e.get("label") or "").lower()
            if label and len(label) >= 3 and label in q and e["id"] not in seen:
                seen[e["id"]] = {"id": e["id"], "label": e["label"], "type": e["type"], "matchedTerm": label, "score": 0.05}
        return list(seen.values())[:max_entities]
    choices = []
    for e in gd["search_index"]:
        if e.get("label"): choices.append((e["label"].lower(), e))
        if e.get("alt"):
            for a in re.split(r"[,;]", e.get("alt") or ""):
                a = a.strip().lower()
                if a: choices.append((a, e))
    choice_strs = [c[0] for c in choices]
    nodeids = set(gd["node_by_id"].keys())
    seen = {}
    for term in ngrams(question):
        is_multi = " " in term
        if not is_multi and len(term) < 4 and not re.search(r"\d", term):
            continue
        bonus = -0.05 if is_multi else 0
        results = fuzzproc.extract(term, choice_strs, scorer=fuzz.ratio, limit=3)
        for _, score, idx in results:
            item = choices[idx][1]
            if item["id"] not in nodeids: continue
            distance = (100 - score)/100 + bonus
            if distance > 0.18: continue
            cur = seen.get(item["id"])
            if not cur or distance < cur["score"]:
                seen[item["id"]] = {"id": item["id"], "label": item["label"], "type": item["type"],
                                     "matchedTerm": term, "score": distance}
    return sorted(seen.values(), key=lambda x: x["score"])[:max_entities]


def neighborhood(gd, seed_ids, hops=1):
    keep = set(seed_ids); frontier = set(seed_ids)
    for _ in range(hops):
        nxt = set()
        for nid in frontier:
            for e in gd["adj"].get(nid, []):
                other = e["target"] if e["source"] == nid else e["source"]
                if other not in keep:
                    keep.add(other); nxt.add(other)
        frontier = nxt
        if not frontier: break
    nodes = [gd["node_by_id"][i] for i in keep if i in gd["node_by_id"]]
    edges = [e for e in gd["edges"] if e["source"] in keep and e["target"] in keep]
    return {"nodes": nodes, "edges": edges}


EDGE_VERB = {
    "linked_to_risk": "is linked to risk of", "affects_response_to": "affects response to",
    "can_trigger": "can trigger", "has_variant": "has variant cluster",
    "belongs_to_class": "belongs to drug class", "associated_with": "is associated with",
}


def render_a3(question, gd, entities):
    lines = [f"QUESTION: {question}", ""]
    if not entities:
        lines.append("ENTITY LINKING: no specific graph entities matched the question.")
        return "\n".join(lines)
    lines.append("ENTITIES IDENTIFIED IN QUESTION:")
    for e in entities:
        lines.append(f'  • {e["label"]} ({e["type"]}) — matched "{e["matchedTerm"]}"')
    lines.append("")
    for ent in entities:
        nb = neighborhood(gd, [ent["id"]], 1)
        lines.append(f'DIRECT NEIGHBOURHOOD OF {ent["label"]}:')
        for e in nb["edges"]:
            if e["source"] != ent["id"] and e["target"] != ent["id"]: continue
            a = gd["node_by_id"].get(e["source"]); b = gd["node_by_id"].get(e["target"])
            if not a or not b: continue
            verb = EDGE_VERB.get(e.get("type",""), e.get("type",""))
            tag = []
            if e.get("level"): tag.append(f"L{e['level']}")
            if e.get("critical"): tag.append("CRITICAL")
            tag_s = f"[{', '.join(tag)}]" if tag else ""
            lines.append(f'  - {a["label"]} --{verb}{tag_s}--> {b["label"]}')
        lines.append("")
    return "\n".join(lines)


# ---------- A1 retrieval — reuse a1_retrieve.py ----------

def a1_context(question):
    from a1_retrieve import retrieve, render_a1_context
    chunks = retrieve(question, top_final=8)
    return render_a1_context(chunks)


# ---------- Claude CLI ----------

def call_claude(system, user, model="sonnet", timeout=180):
    args = ["claude", "-p", "--output-format", "json", "--model", model,
            "--append-system-prompt", system,
            "--disallowedTools",
            "Read Write Edit Bash WebSearch WebFetch Agent TaskCreate TaskUpdate TaskList"]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=user, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", int((time.time()-t0)*1000), "timeout"
    dur = int((time.time()-t0)*1000)
    if r.returncode != 0:
        return "", dur, f"exit {r.returncode}: {r.stderr[:200]}"
    try:
        j = json.loads(r.stdout)
    except Exception as e:
        return "", dur, f"json parse: {e}"
    if j.get("is_error"):
        return "", dur, "claude is_error"
    return j.get("result", ""), j.get("duration_ms", dur), None


def build_user(context, question):
    return f"""Context:
---
{context}
---

User question: {question}"""


def normalize(text):
    t = text or ""
    t = re.sub(r"\[PMID[:\s]*(\d+)\]", r"(cite \1)", t, flags=re.I)
    t = re.sub(r"\bPMID[:\s]*(\d+)\b", r"(cite \1)", t, flags=re.I)
    t = re.sub(r"^\s*[-•·*]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    return t.replace("**", "").replace("__", "").strip()


def call_judge(question, ans_a, ans_b, model="haiku"):
    user = f"""Question: {question}

Answer A:
{normalize(ans_a)}

Answer B:
{normalize(ans_b)}

Pick: """
    text, dur, err = call_claude(JUDGE_SYSTEM, user, model=model, timeout=90)
    if err or not text:
        return "TIE", err or "empty", dur
    first = text.split("\n", 1)[0].strip().upper()
    if first in ("A", "B", "TIE"):
        pick, reason = first, (text.split("\n",1)[1].strip() if "\n" in text else "")
    elif first.startswith("A"): pick, reason = "A", text
    elif first.startswith("B"): pick, reason = "B", text
    else: pick, reason = "TIE", text[:200]
    return pick, reason[:280], dur


# ---------- Driver ----------

def main():
    if not CANDS.exists():
        print(f"missing {CANDS}", file=sys.stderr)
        return 1
    cands = [json.loads(l) for l in CANDS.read_text().splitlines() if l.strip()]

    print("loading FULL graph for A3...", file=sys.stderr)
    gd = load_graph()
    print(f"  {len(gd['nodes'])} nodes, {len(gd['edges'])} edges", file=sys.stderr)

    ANS.unlink(missing_ok=True); JDG.unlink(missing_ok=True)

    # ---- Generate answers ----
    t0 = time.time()
    print(f"\ngenerating {len(cands)*3} answers (serial)...", file=sys.stderr)
    answers_by_key = {}
    for i, c in enumerate(cands, 1):
        for arm in ("A0", "A1", "A3"):
            if arm == "A0":
                ctx = A0_NOCTX
                extra = {}
            elif arm == "A1":
                ctx = a1_context(c["question"])
                extra = {"context_chars": len(ctx)}
            else:
                ents = link_entities(c["question"], gd)
                ctx = render_a3(c["question"], gd, ents)
                extra = {"context_chars": len(ctx), "entities_linked": [e["label"] for e in ents]}
            user = build_user(ctx, c["question"])
            text, dur, err = call_claude(CHAT_SYSTEM, user, model="sonnet")
            rec = {"question_id": c["id"], "pattern": c["pattern"], "arm": arm,
                   "answer": text, "duration_ms": dur, "error": err, **extra}
            with ANS.open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            answers_by_key[(c["id"], arm)] = rec
            status = "✗ " + (err or "")[:50] if err else f"✓ {len(text)} chars"
            elapsed = time.time() - t0
            total_calls = i*3 + ["A0","A1","A3"].index(arm) - 2
            eta = (len(cands)*3 - total_calls) * (elapsed/max(total_calls,1))
            print(f"  [{c['id']} {arm}]  {dur/1000:.1f}s  {status}  · eta {eta/60:.1f}m", file=sys.stderr)

    # ---- Judge ----
    print(f"\njudging {len(cands)*2} pairs...", file=sys.stderr)
    rng = random.Random(42)
    tj0 = time.time()
    j_count = 0
    for c in cands:
        for pair in ("A3_vs_A1", "A3_vs_A0"):
            armA, armB = pair.split("_vs_")
            if (c["id"], armA) not in answers_by_key or (c["id"], armB) not in answers_by_key:
                continue
            swap = rng.random() < 0.5
            shown_a_arm, shown_b_arm = (armB, armA) if swap else (armA, armB)
            ans_a = answers_by_key[(c["id"], shown_a_arm)]["answer"]
            ans_b = answers_by_key[(c["id"], shown_b_arm)]["answer"]
            pick, reason, dur = call_judge(c["question"], ans_a, ans_b)
            target = shown_a_arm if pick == "A" else (shown_b_arm if pick == "B" else "TIE")
            rec = {"question_id": c["id"], "pattern": c["pattern"], "pair": pair,
                   "shown_a_arm": shown_a_arm, "shown_b_arm": shown_b_arm,
                   "pick": pick, "target_pick": target, "reason": reason, "duration_ms": dur}
            with JDG.open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            j_count += 1
            elapsed = time.time() - tj0
            eta = (len(cands)*2 - j_count) * (elapsed/max(j_count,1))
            print(f"  [{c['id']} {pair:11}]  {dur/1000:.1f}s  → {target}  · eta {eta/60:.1f}m", file=sys.stderr)

    print(f"\ndone. total wall time {(time.time()-t0)/60:.1f}m", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
