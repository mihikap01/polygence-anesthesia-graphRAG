#!/usr/bin/env python3
"""
Minimal Python port of web/lib/graph/retrieve.ts — just what A3 needs.

Loads the held-out graph + search index, exposes:
  - link_entities(question) -> list[entity dict]
  - neighborhood(seeds, hops=1) -> Graph
  - shortest_path(from_id, to_id, max_hops=4) -> Path | None
  - render_a3_context(question, entities, neighborhoods, paths) -> str
      (structured packet — what the system actually sends to Claude)
  - render_a2_context(seed_graph) -> str
      (flat dump of the anesthesia seed subgraph)

Mirrors the TS code's structure so the eval tests the real retrieval logic,
not a hand-waved approximation.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from rapidfuzz import fuzz, process as fuzzproc
except ImportError:
    fuzz = None
    fuzzproc = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EDGE_VERB = {
    "linked_to_risk": "is linked to risk of",
    "affects_response_to": "affects response to",
    "can_trigger": "can trigger",
    "has_variant": "has variant cluster",
    "belongs_to_class": "belongs to drug class",
    "associated_with": "is associated with",
}

STOPWORDS = {
    "a","an","the","is","are","was","were","be","been","being",
    "of","to","for","in","on","at","by","with","from","and","or",
    "but","if","then","than","so","as","it","this","that","these",
    "those","what","which","who","whom","whose","where","when",
    "why","how","does","do","did","can","could","should","would",
    "will","have","has","had","i","you","we","they","he","she",
    "me","us","them","him","her","between","about","into","through",
    "during","before","after","above","below","tell","explain","show",
    "list","give","find","say","see","look","describe","any","some",
    "all","most","more","less","very","patient","class","gene","drug",
    "phenotype","variant","carry","find","carries","carrying",
    "pharmacogenomic","association","associations","association",
    "reference","references","cite","citation","clinical","evidence",
    "guideline","guidelines","level","interaction","interactions",
}


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------

@dataclass
class GraphData:
    nodes: list[dict]
    edges: list[dict]
    node_by_id: dict[str, dict]
    adj: dict[str, list[dict]]   # node_id -> list of edges
    search_index: list[dict]     # [{id, label, type, alt}]


def load_heldout() -> GraphData:
    g_path = DATA / "graph_heldout.json"
    si_path = DATA / "search_index_heldout.json"
    if not g_path.exists() or not si_path.exists():
        raise SystemExit("missing held-out artifacts; run eval/rebuild_heldout.py first")
    g = json.loads(g_path.read_text())
    si = json.loads(si_path.read_text())
    node_by_id = {n["id"]: n for n in g["nodes"]}
    adj: dict[str, list[dict]] = defaultdict(list)
    for e in g["edges"]:
        adj[e["source"]].append(e)
        adj[e["target"]].append(e)
    return GraphData(g["nodes"], g["edges"], node_by_id, adj, si)


def load_seed() -> dict:
    p = DATA / "seed_anesthesia_heldout.json"
    if not p.exists():
        raise SystemExit("missing seed_anesthesia_heldout.json")
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Entity linking (mirrors retrieve.ts extractEntities)
# ---------------------------------------------------------------------------

def _ngrams(question: str) -> list[str]:
    tokens = [t for t in re.split(r"\s+", re.sub(r"[^\w\s-]", " ", question.lower()))
              if len(t) >= 3 and t not in STOPWORDS]
    grams: list[str] = []
    for n in (3, 2, 1):
        for i in range(0, len(tokens) - n + 1):
            grams.append(" ".join(tokens[i:i+n]))
    # de-dup preserving order
    seen = set(); out = []
    for g in grams:
        if g not in seen:
            seen.add(g); out.append(g)
    return out


def link_entities(question: str, gd: GraphData, max_entities: int = 6) -> list[dict]:
    """Return list of {id, label, type, matchedTerm, score} like the TS code."""
    if fuzzproc is None:
        return _link_simple(question, gd, max_entities)
    # Build choices: each search-index entry, indexed by label and alt
    choices: list[tuple[str, dict]] = []
    for e in gd.search_index:
        if e.get("label"):
            choices.append((e["label"].lower(), e))
        if e.get("alt"):
            for a in re.split(r"[,;]", e.get("alt") or ""):
                a = a.strip().lower()
                if a:
                    choices.append((a, e))
    choice_strs = [c[0] for c in choices]
    nodeids = set(gd.node_by_id.keys())
    grams = _ngrams(question)
    seen: dict[str, dict] = {}
    for term in grams:
        is_multi = " " in term
        if not is_multi and len(term) < 4 and not re.search(r"\d", term):
            continue
        bonus = -0.05 if is_multi else 0
        # rapidfuzz: token-set / ratio. Use ratio with a tight threshold.
        results = fuzzproc.extract(term, choice_strs, scorer=fuzz.ratio, limit=3)
        for _, score, idx in results:
            item = choices[idx][1]
            if item["id"] not in nodeids:
                continue
            # Convert rapidfuzz 0-100 to Fuse-like 0-1 distance
            distance = (100 - score) / 100 + bonus
            if distance > 0.18:
                continue
            cur = seen.get(item["id"])
            if not cur or distance < cur["score"]:
                seen[item["id"]] = {
                    "id": item["id"], "label": item["label"], "type": item["type"],
                    "matchedTerm": term, "score": distance,
                }
    out = sorted(seen.values(), key=lambda x: x["score"])[:max_entities]
    return out


def _link_simple(question: str, gd: GraphData, max_entities: int = 6) -> list[dict]:
    """Fallback if rapidfuzz isn't installed — exact substring match."""
    q = question.lower()
    seen: dict[str, dict] = {}
    for e in gd.search_index:
        label = (e.get("label") or "").lower()
        if not label or len(label) < 3:
            continue
        if label in q and e["id"] not in seen:
            seen[e["id"]] = {
                "id": e["id"], "label": e["label"], "type": e["type"],
                "matchedTerm": label, "score": 0.05,
            }
    return list(seen.values())[:max_entities]


# ---------------------------------------------------------------------------
# Subgraph + path operations
# ---------------------------------------------------------------------------

def neighborhood(gd: GraphData, seeds: list[str], hops: int = 1) -> dict:
    keep = set(seeds)
    frontier = set(seeds)
    for _ in range(hops):
        nxt = set()
        for nid in frontier:
            for e in gd.adj.get(nid, []):
                other = e["target"] if e["source"] == nid else e["source"]
                if other not in keep:
                    keep.add(other)
                    nxt.add(other)
        frontier = nxt
        if not frontier:
            break
    nodes = [gd.node_by_id[i] for i in keep if i in gd.node_by_id]
    edges = [e for e in gd.edges if e["source"] in keep and e["target"] in keep]
    return {"nodes": nodes, "edges": edges}


def shortest_path(gd: GraphData, from_id: str, to_id: str, max_hops: int = 4) -> dict | None:
    if from_id == to_id:
        return None
    parent: dict[str, tuple[str, dict] | None] = {from_id: None}
    q = deque([(from_id, 0)])
    while q:
        n, d = q.popleft()
        if n == to_id:
            break
        if d >= max_hops:
            continue
        for e in gd.adj.get(n, []):
            other = e["target"] if e["source"] == n else e["source"]
            if other in parent:
                continue
            parent[other] = (n, e)
            q.append((other, d + 1))
    if to_id not in parent:
        return None
    node_ids: list[str] = []
    edges: list[dict] = []
    cur = to_id
    while cur is not None:
        node_ids.append(cur)
        p = parent[cur]
        if p is None:
            break
        edges.append(p[1])
        cur = p[0]
    node_ids.reverse(); edges.reverse()
    return {
        "from_id": from_id, "to_id": to_id,
        "nodes": [gd.node_by_id[i] for i in node_ids if i in gd.node_by_id],
        "edges": edges,
        "hops": len(edges),
        "critical": any(e.get("critical") or e.get("level") in ("1A", "1B") for e in edges),
    }


# ---------------------------------------------------------------------------
# Context rendering
# ---------------------------------------------------------------------------

def _render_edge_line(a: dict, e: dict, b: dict) -> str:
    verb = EDGE_VERB.get(e.get("type", ""), e.get("type", ""))
    tag = []
    if e.get("level"): tag.append(f"L{e['level']}")
    if e.get("role"): tag.append(e["role"])
    if e.get("critical"): tag.append("CRITICAL")
    if e.get("count"): tag.append(f"{e['count']} variants")
    tag_s = f"[{', '.join(tag)}]" if tag else ""
    return f"{a['label']} --{verb}{tag_s}--> {b['label']}"


def render_a3_context(question: str, gd: GraphData, entities: list[dict],
                      neighborhoods: list[dict], paths: list[dict]) -> str:
    """Same shape as retrieve.ts retrieveForQuestion's contextText."""
    lines = [f"QUESTION: {question}", ""]
    if not entities:
        lines.append("ENTITY LINKING: no specific graph entities matched the question.")
        return "\n".join(lines)
    lines.append("ENTITIES IDENTIFIED IN QUESTION:")
    for e in entities:
        lines.append(f"  • {e['label']} ({e['type']}) — matched \"{e['matchedTerm']}\"")
    lines.append("")
    for ent, nb in zip(entities, neighborhoods):
        lines.append(f"DIRECT NEIGHBOURHOOD OF {ent['label']}:")
        for ed in nb["edges"]:
            if ed["source"] != ent["id"] and ed["target"] != ent["id"]:
                continue
            a = gd.node_by_id.get(ed["source"])
            b = gd.node_by_id.get(ed["target"])
            if not a or not b:
                continue
            lines.append(f"  - {_render_edge_line(a, ed, b)}")
        lines.append("")
    if paths:
        lines.append(f"SHORTEST REASONING PATHS ({len(paths)}):")
        for p in paths:
            a = gd.node_by_id.get(p["from_id"]); b = gd.node_by_id.get(p["to_id"])
            if not a or not b: continue
            critical = ", CRITICAL" if p["critical"] else ""
            lines.append(f"  {a['label']} → {b['label']} ({p['hops']} hop(s){critical}):")
            for i, e in enumerate(p["edges"]):
                lines.append(f"    {_render_edge_line(p['nodes'][i], e, p['nodes'][i+1])}")
        lines.append("")
    return "\n".join(lines)


def render_a2_context(seed_graph: dict) -> str:
    """A2: dump the anesthesia seed subgraph as text. Mirrors how the original
    web UI's 'visibleGraphContext' formats things — flat lines, no structure."""
    lines = ["VISIBLE GRAPH (anesthesia seed subgraph):"]
    by_id = {n["id"]: n for n in seed_graph["nodes"]}
    by_type: dict[str, list[dict]] = defaultdict(list)
    for n in seed_graph["nodes"]:
        by_type[n["type"]].append(n)
    for t, ns in by_type.items():
        sample = ", ".join(n["label"] for n in ns[:12])
        more = ", …" if len(ns) > 12 else ""
        lines.append(f"  - {t}: {len(ns)} ({sample}{more})")
    lines.append("")
    lines.append("Relationships:")
    for e in seed_graph["edges"]:
        a = by_id.get(e["source"]); b = by_id.get(e["target"])
        if not a or not b: continue
        verb = EDGE_VERB.get(e.get("type", ""), e.get("type", ""))
        tag = []
        if e.get("level"): tag.append(f"L{e['level']}")
        if e.get("critical"): tag.append("CRITICAL")
        tag_s = f" [{','.join(tag)}]" if tag else ""
        lines.append(f"  - {a['label']} {verb} {b['label']}{tag_s}")
    return "\n".join(lines)


def retrieve_for_question(question: str, gd: GraphData,
                          max_path_hops: int = 4) -> tuple[str, list[dict], list[dict], list[dict]]:
    """Full GraphRAG retrieval pipeline. Returns (context_text, entities, neighborhoods, paths)."""
    entities = link_entities(question, gd)
    neighborhoods = []
    for e in entities:
        neighborhoods.append(neighborhood(gd, [e["id"]], hops=1))
    paths = []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            p = shortest_path(gd, entities[i]["id"], entities[j]["id"], max_path_hops)
            if p:
                paths.append(p)
    paths.sort(key=lambda p: (not p["critical"], p["hops"]))
    ctx = render_a3_context(question, gd, entities, neighborhoods, paths)
    return ctx, entities, neighborhoods, paths


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    gd = load_heldout()
    print(f"loaded held-out graph: {len(gd.nodes)} nodes, {len(gd.edges)} edges,"
          f" search index: {len(gd.search_index)}", file=sys.stderr)
    q = "Why is sevoflurane risky for malignant hyperthermia?"
    ctx, ents, nbh, paths = retrieve_for_question(q, gd)
    print(f"--- question: {q}")
    print(f"--- entities: {[(e['label'], e['type'], round(e['score'], 3)) for e in ents]}")
    print(f"--- paths: {len(paths)}")
    print(f"--- context ({len(ctx)} chars):")
    print(ctx[:1200])
