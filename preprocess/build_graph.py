#!/usr/bin/env python3
"""
Builds a simplified pharmacogenomic reasoning graph from the PharmGKB-style TSV
data shipped in this repo, optimized for human interpretability rather than a
raw network dump.

Inputs  (../):  relationships.tsv, clinicalVariants.tsv,
                drugs.tsv, chemicals.tsv, genes.tsv, phenotypes.tsv
Outputs (../data/): graph.json, seed_anesthesia.json, search_index.json

The simplification rules:

  * `clinicalVariants.tsv` is the backbone — it is curated, evidence-graded,
    and ties drugs to genes/variants to phenotypes.
  * `relationships.tsv` is used to harvest PMIDs and to fill out broader
    drug<->gene context.  "not associated" and "ambiguous" rows are dropped
    by default.
  * Variants for the same gene+drug+phenotype combo are collapsed into a
    single expandable cluster node ("RYR1 variants (N)").
  * A handful of curated drug-class nodes are injected so the anesthesia
    subgraph reads cleanly: Volatile Anesthetics, Depolarizing NMBs, etc.
  * Edge type is derived from the clinical-variant `type` + level of
    evidence; level 1A/1B Toxicity → `linked_to_risk` (highlighted red).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = ROOT / "data"

# Eval support: when HELDOUT_VARIANTS=<path> is set, skip clinicalVariants
# rows whose stable hash appears in that file (one hash per line). Used by
# eval/rebuild_heldout.py to produce a reduced graph for the held-out test.
# Also honors HELDOUT_OUTPUT to write to a different file than data/graph.json.
HELDOUT_FILE = os.environ.get("HELDOUT_VARIANTS")
HELDOUT_OUTPUT_NAME = os.environ.get("HELDOUT_OUTPUT")  # e.g. "graph_heldout.json"

def _row_hash(row: dict) -> str:
    """Stable hash of a clinicalVariants row, independent of column order."""
    keys = ("variant", "gene", "type", "level of evidence", "chemicals", "phenotypes")
    return "|".join((row.get(k) or "").strip() for k in keys)

_HELDOUT_HASHES: set[str] = set()
if HELDOUT_FILE:
    with open(HELDOUT_FILE) as _fh:
        _HELDOUT_HASHES = {ln.strip() for ln in _fh if ln.strip()}
    print(f"[heldout] loaded {len(_HELDOUT_HASHES)} hashes from {HELDOUT_FILE}", file=sys.stderr)

# --- curated drug-class definitions for the anesthesia demo ----------------
DRUG_CLASSES: dict[str, list[str]] = {
    "Volatile Anesthetics": [
        "sevoflurane", "isoflurane", "desflurane", "halothane",
        "enflurane", "methoxyflurane",
    ],
    "Depolarizing Neuromuscular Blockers": ["succinylcholine"],
    "Thiopurines": ["azathioprine", "mercaptopurine", "thioguanine"],
    "Fluoropyrimidines": ["fluorouracil", "capecitabine", "tegafur"],
    "Vitamin K Antagonists": ["warfarin", "acenocoumarol", "phenprocoumon"],
    "Opioids": ["codeine", "tramadol", "oxycodone", "morphine", "fentanyl"],
    "SSRIs": [
        "fluoxetine", "sertraline", "paroxetine", "citalopram",
        "escitalopram", "fluvoxamine",
    ],
    "Statins": [
        "simvastatin", "atorvastatin", "rosuvastatin", "pravastatin",
        "fluvastatin", "lovastatin",
    ],
}

# Evidence rank: lower = stronger
LEVEL_RANK = {"1A": 1, "1B": 2, "2A": 3, "2B": 4, "3": 5, "4": 6}

ANESTHESIA_SEED_DRUGS = {d for ds in (DRUG_CLASSES["Volatile Anesthetics"],
                                     DRUG_CLASSES["Depolarizing Neuromuscular Blockers"])
                        for d in ds}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def slug(*parts: str) -> str:
    raw = "_".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9_:.-]+", "_", raw).strip("_").lower()


def split_csv_field(value: str) -> list[str]:
    """Split a comma-separated cell, respecting a couple of common quirks."""
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def read_tsv(path: Path) -> Iterable[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            yield row


# ---------------------------------------------------------------------------
# 1.  load reference metadata
# ---------------------------------------------------------------------------

def load_genes() -> dict[str, dict]:
    out = {}
    for row in read_tsv(ROOT / "genes.tsv"):
        sym = (row.get("Symbol") or "").strip()
        if not sym:
            continue
        out[sym.upper()] = {
            "name": row.get("Name", "").strip(),
            "pharmgkb_id": row.get("PharmGKB Accession Id", "").strip(),
            "ncbi_id": row.get("NCBI Gene ID", "").strip(),
            "hgnc": row.get("HGNC ID", "").strip(),
            "chromosome": row.get("Chromosome", "").strip(),
            "is_vip": (row.get("Is VIP", "").strip().lower() == "yes"),
            "has_cpic": (row.get("Has CPIC Dosing Guideline", "").strip().lower() == "yes"),
        }
    return out


def load_drugs() -> dict[str, dict]:
    """Index drugs by lowercased name AND by generic-name aliases."""
    out = {}
    for row in read_tsv(ROOT / "drugs.tsv"):
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        meta = {
            "name": name,
            "pharmgkb_id": row.get("PharmGKB Accession Id", "").strip(),
            "type": row.get("Type", "").strip(),
            "atc": row.get("ATC Identifiers", "").strip(),
            "rxnorm": row.get("RxNorm Identifiers", "").strip(),
            "top_clinical_level": row.get("Top Clinical Annotation Level", "").strip(),
            "has_dosing": (row.get("Label Has Dosing Info", "").strip().lower() == "yes"),
        }
        out[name.lower()] = meta
        # alt names
        for alt in split_csv_field(row.get("Generic Names", "").strip('"')):
            alt = alt.strip().strip('"')
            if alt and alt.lower() not in out:
                out[alt.lower()] = meta
    return out


def load_phenotypes() -> dict[str, dict]:
    out = {}
    for row in read_tsv(ROOT / "phenotypes.tsv"):
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        out[name.lower()] = {
            "name": name,
            "pharmgkb_id": row.get("PharmGKB Accession Id", "").strip(),
            "xref": row.get("External Vocabulary", "").strip(),
        }
    return out


# ---------------------------------------------------------------------------
# 2.  build the simplified graph
# ---------------------------------------------------------------------------

def build_graph(genes, drugs, phenotypes):
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    # for variant clustering
    cluster_buckets: dict[tuple, list[dict]] = defaultdict(list)

    def add_node(node_id: str, label: str, ntype: str, **extra) -> str:
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "label": label,
                "type": ntype,
                **extra,
            }
        else:
            # merge extras non-destructively
            for k, v in extra.items():
                if v and not nodes[node_id].get(k):
                    nodes[node_id][k] = v
        return node_id

    edge_index: dict[tuple, dict] = {}

    def add_edge(source, target, etype, **extra):
        key = (source, target, etype)
        existing = edge_index.get(key)
        if existing is None:
            e = {
                "id": f"e{len(edges)}",
                "source": source,
                "target": target,
                "type": etype,
                **extra,
            }
            edges.append(e)
            edge_index[key] = e
            return
        # merge — keep the strongest evidence level, accumulate role/count
        if "level" in extra and LEVEL_RANK.get(extra["level"], 99) < LEVEL_RANK.get(existing.get("level", ""), 99):
            existing["level"] = extra["level"]
        if extra.get("critical"):
            existing["critical"] = True
        if "count" in extra:
            existing["count"] = existing.get("count", 0) + extra["count"]
        if "role" in extra and extra["role"] not in (existing.get("role") or ""):
            existing["role"] = (existing.get("role", "") + "," + extra["role"]).strip(",")
        if "gene" in extra and extra["gene"] not in (existing.get("gene") or ""):
            existing["gene"] = (existing.get("gene", "") + "," + extra["gene"]).strip(",")

    # ---- drug-class nodes -------------------------------------------------
    drug_to_class: dict[str, str] = {}
    for cls_name, members in DRUG_CLASSES.items():
        cls_id = slug("class", cls_name)
        add_node(cls_id, cls_name, "drug_class",
                 description=f"Drug class: {cls_name}")
        for m in members:
            drug_to_class[m.lower()] = cls_id

    # ---- clinical-variants backbone --------------------------------------
    clin_rows = list(read_tsv(ROOT / "clinicalVariants.tsv"))
    if _HELDOUT_HASHES:
        before = len(clin_rows)
        clin_rows = [r for r in clin_rows if _row_hash(r) not in _HELDOUT_HASHES]
        print(f"[heldout] dropped {before - len(clin_rows)} clinicalVariants rows", file=sys.stderr)
    # we'll iterate over (gene, row) pairs so a multi-gene row writes one edge
    # per gene rather than a synthetic compound-gene node.
    expanded_rows: list[tuple[str, dict]] = []
    for row in clin_rows:
        gene_field = (row.get("gene") or "").strip()
        if not gene_field:
            continue
        for g in re.split(r"[,;]", gene_field):
            g = g.strip().upper()
            if g:
                expanded_rows.append((g, row))

    for gene_sym, row in expanded_rows:
        variant_field = (row.get("variant") or "").strip()
        cv_type = (row.get("type") or "").strip()
        level = (row.get("level of evidence") or "").strip()
        chem_field = (row.get("chemicals") or "").strip()
        phen_field = (row.get("phenotypes") or "").strip()

        if not variant_field or not chem_field:
            continue

        # gene node
        gene_meta = genes.get(gene_sym, {})
        gene_id = slug("gene", gene_sym)
        add_node(gene_id, gene_sym, "gene",
                 fullName=gene_meta.get("name", gene_sym),
                 pharmgkb_id=gene_meta.get("pharmgkb_id", ""),
                 chromosome=gene_meta.get("chromosome", ""),
                 is_vip=gene_meta.get("is_vip", False))

        # variant token(s) — sometimes a row lists multiple star alleles
        variant_tokens = [v.strip() for v in re.split(r"[,;]", variant_field) if v.strip()]

        # phenotype list
        phen_names = split_csv_field(phen_field.strip('"'))
        phen_nodes = []
        for phen in phen_names:
            phen_id = slug("phen", phen)
            phen_meta = phenotypes.get(phen.lower(), {})
            add_node(phen_id, phen, "phenotype",
                     pharmgkb_id=phen_meta.get("pharmgkb_id", ""),
                     xref=phen_meta.get("xref", ""))
            phen_nodes.append(phen_id)

        # chemicals
        chem_names = split_csv_field(chem_field.strip('"'))
        for chem in chem_names:
            chem_lower = chem.lower()
            drug_meta = drugs.get(chem_lower, {})
            chem_id = slug("drug", chem)
            add_node(chem_id, chem, "drug",
                     pharmgkb_id=drug_meta.get("pharmgkb_id", ""),
                     atc=drug_meta.get("atc", ""),
                     top_level=drug_meta.get("top_clinical_level", ""))

            # link drug to class
            cls = drug_to_class.get(chem_lower)
            if cls:
                add_edge(chem_id, cls, "belongs_to_class")

            # edge type from cv_type / level
            is_toxic = "Toxicity" in cv_type
            critical = is_toxic and level in {"1A", "1B"}
            etype = "linked_to_risk" if critical else "affects_response_to"

            # drug → gene
            add_edge(chem_id, gene_id, etype,
                     level=level, role=cv_type, critical=critical)

            # one cluster per gene — accumulate variants across drugs/levels
            for vt in variant_tokens:
                cluster_buckets[(gene_id,)].append({
                    "rsid": vt,
                    "gene": gene_sym,
                    "level": level,
                    "type": cv_type,
                    "chemical": chem,
                    "phenotypes": phen_nodes,
                })

            # drug/gene → phenotype (toxicity risk)
            for phen_id in phen_nodes:
                if critical:
                    add_edge(chem_id, phen_id, "can_trigger",
                             level=level, gene=gene_sym)
                else:
                    add_edge(gene_id, phen_id, "associated_with",
                             level=level, role=cv_type)

    # ---- create variant cluster nodes (one per gene) ---------------------
    for (gene_id,), variants in cluster_buckets.items():
        gene_sym = nodes[gene_id]["label"]
        # de-duplicate variant tokens (a single rsID can appear under
        # multiple drugs/phenotypes in clinicalVariants)
        unique_members = {}
        for v in variants:
            if v["rsid"] not in unique_members or \
               LEVEL_RANK.get(v["level"], 99) < LEVEL_RANK.get(unique_members[v["rsid"]]["level"], 99):
                unique_members[v["rsid"]] = v
        members = list(unique_members.values())
        best_level = min((m["level"] for m in members if m["level"]),
                         key=lambda lv: LEVEL_RANK.get(lv, 99), default="")
        cluster_id = slug("vcluster", gene_id)
        cluster_label = f"{gene_sym} variants ({len(members)})"
        add_node(cluster_id, cluster_label, "variant_cluster",
                 gene=gene_sym, level=best_level,
                 members=[
                     {"rsid": m["rsid"], "level": m["level"],
                      "role": m["type"], "chemical": m["chemical"]}
                     for m in members
                 ])
        add_edge(gene_id, cluster_id, "has_variant",
                 level=best_level, count=len(members))
        # critical (any 1A/1B Toxicity) → link cluster to those phenotypes
        critical_phens = {p for m in members
                          if m["level"] in {"1A", "1B"} and "Toxicity" in m["type"]
                          for p in m["phenotypes"]}
        for pid in critical_phens:
            add_edge(cluster_id, pid, "linked_to_risk", critical=True)

    # ---- harvest PMIDs from relationships.tsv ----------------------------
    pmid_edges: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in read_tsv(ROOT / "relationships.tsv"):
        assoc = (row.get("Association") or "").strip().lower()
        if assoc in {"not associated", "ambiguous", ""}:
            continue
        pmids = (row.get("PMIDs") or "").strip()
        if not pmids:
            continue
        e1n = (row.get("Entity1_name") or "").strip()
        e2n = (row.get("Entity2_name") or "").strip()
        e1t = (row.get("Entity1_type") or "").strip()
        e2t = (row.get("Entity2_type") or "").strip()
        # map relationships entities onto our drug/gene/phenotype IDs
        def map_entity(name, etype):
            if etype == "Gene":
                gid = slug("gene", name.upper())
                return gid if gid in nodes else None
            if etype == "Chemical":
                did = slug("drug", name)
                return did if did in nodes else None
            if etype == "Disease":
                pid = slug("phen", name)
                return pid if pid in nodes else None
            return None
        a = map_entity(e1n, e1t)
        b = map_entity(e2n, e2t)
        if not a or not b or a == b:
            continue
        key = tuple(sorted([a, b]))
        for pmid in pmids.split(";"):
            pmid = pmid.strip()
            if pmid:
                pmid_edges[key].add(pmid)

    # attach pmids onto existing edges where endpoints match
    edge_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        edge_by_pair[tuple(sorted([e["source"], e["target"]]))].append(e)
    for key, pmids in pmid_edges.items():
        for e in edge_by_pair.get(key, []):
            e.setdefault("pmids", [])
            e["pmids"] = sorted(set(e["pmids"]) | pmids)[:25]

    return nodes, edges


# ---------------------------------------------------------------------------
# 3.  build the anesthesia seed view
# ---------------------------------------------------------------------------

def build_seed(nodes, edges):
    """Tight anesthesia/MH demo seed — only the high-evidence reasoning chain.

    drugs → genes (via linked_to_risk/affects_response_to where the drug is
    one of the seeded anesthetics)
    genes → variant_clusters (via has_variant, only those tied to seed drugs)
    drugs → phenotypes (via can_trigger)
    """
    seed_drug_ids = {slug("drug", d) for d in ANESTHESIA_SEED_DRUGS}
    seed_drug_ids &= set(nodes.keys())

    keep = set(seed_drug_ids)
    keep.add(slug("class", "Volatile Anesthetics"))
    keep.add(slug("class", "Depolarizing Neuromuscular Blockers"))

    # genes & phenotypes reachable from seed drugs via clinical-variant edges
    relevant_edge_types = {
        "linked_to_risk", "affects_response_to", "can_trigger",
        "belongs_to_class", "associated_with",
    }
    genes_from_seed = set()
    phens_from_seed = set()
    for e in edges:
        if e["type"] not in relevant_edge_types:
            continue
        if e["source"] in seed_drug_ids and nodes[e["target"]]["type"] == "gene":
            genes_from_seed.add(e["target"])
        elif e["target"] in seed_drug_ids and nodes[e["source"]]["type"] == "gene":
            genes_from_seed.add(e["source"])
        if e["source"] in seed_drug_ids and nodes[e["target"]]["type"] == "phenotype":
            phens_from_seed.add(e["target"])
        elif e["target"] in seed_drug_ids and nodes[e["source"]]["type"] == "phenotype":
            phens_from_seed.add(e["source"])
    keep.update(genes_from_seed)
    keep.update(phens_from_seed)

    # variant clusters tied to those genes (only clusters whose has_variant
    # edge connects a kept gene)
    cluster_ids = set()
    for e in edges:
        if e["type"] != "has_variant":
            continue
        if e["source"] in genes_from_seed and nodes[e["target"]]["type"] == "variant_cluster":
            cluster_ids.add(e["target"])
    keep.update(cluster_ids)

    seed_nodes = [nodes[n] for n in keep if n in nodes]
    seed_edges = [
        e for e in edges
        if e["source"] in keep and e["target"] in keep
    ]
    return {"nodes": seed_nodes, "edges": seed_edges}


# ---------------------------------------------------------------------------
# 4.  build a search index
# ---------------------------------------------------------------------------

def build_search_index(nodes):
    items = []
    for n in nodes.values():
        items.append({
            "id": n["id"],
            "label": n["label"],
            "type": n["type"],
            "alt": n.get("fullName", ""),
        })
    items.sort(key=lambda x: (x["type"], x["label"].lower()))
    return items


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    print("Loading reference metadata...", file=sys.stderr)
    genes = load_genes()
    drugs = load_drugs()
    phenotypes = load_phenotypes()
    print(f"  genes={len(genes)}  drugs={len(drugs)}  phenotypes={len(phenotypes)}",
          file=sys.stderr)

    print("Building simplified graph...", file=sys.stderr)
    nodes, edges = build_graph(genes, drugs, phenotypes)
    print(f"  nodes={len(nodes)}  edges={len(edges)}", file=sys.stderr)

    print("Building anesthesia seed view...", file=sys.stderr)
    seed = build_seed(nodes, edges)
    print(f"  seed nodes={len(seed['nodes'])}  seed edges={len(seed['edges'])}",
          file=sys.stderr)

    out_name = HELDOUT_OUTPUT_NAME or "graph.json"
    print(f"Writing JSON artifacts to data/ (graph file: {out_name})...", file=sys.stderr)
    (DATA_OUT / out_name).write_text(
        json.dumps({"nodes": list(nodes.values()), "edges": edges}, indent=None)
    )
    (DATA_OUT / "seed_anesthesia.json").write_text(json.dumps(seed, indent=None))
    (DATA_OUT / "search_index.json").write_text(
        json.dumps(build_search_index(nodes), indent=None)
    )
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
