#!/usr/bin/env python3
"""
Question generator for the GraphRAG eval.

Reads the held-out clinicalVariants rows (eval/heldout_variants.tsv) and
produces ~200 question/gold pairs across 8 strata. Output is JSONL with one
record per line:

  {
    "id": "S3-042",
    "stratum": "S3",
    "question": "If a patient has variant rs193922832 in RYR1, ...",
    "gold": {
      "entities": ["RYR1", "sevoflurane", "Malignant Hyperthermia"],
      "evidence_level": "1A",
      "pmids": ["12345", "67890"],
      "should_refuse": false,
      "answer_summary": "Avoid volatile anesthetics; risk of MH crisis."
    },
    "source_row_hash": "rs193922832|RYR1|Toxicity|1A|...",
    "template": "s3_variant_to_class_avoidance"
  }

Strata + counts:
  S1 Well-known facts          (n=20)
  S2 Specific evidence levels  (n=25)
  S3 Multi-hop                 (n=40)  — drives H2
  S4 Citation grounding        (n=25)  — needs PMID lookup from relationships.tsv
  S5 Long-tail / niche         (n=25)
  S6 Negative controls         (n=25)  — drawn from relationships.tsv "not associated"
  S7 Out-of-distribution       (n=15)  — drugs not in PharmGKB clinicalVariants
  S8 Comparative               (n=25)

Determinism: GEN_SEED env var (default 7).
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
HELDOUT = EVAL / "heldout_variants.tsv"
FULL_CLIN = ROOT / "clinicalVariants.tsv"
RELATIONSHIPS = ROOT / "relationships.tsv"
DRUGS_TSV = ROOT / "drugs.tsv"
OUT = EVAL / "questions.jsonl"

SEED = int(os.environ.get("GEN_SEED", "7"))

# Curated drug-class memberships (mirror preprocess/build_graph.py)
DRUG_CLASSES = {
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
DRUG_TO_CLASS: dict[str, str] = {
    d.lower(): cls for cls, drugs in DRUG_CLASSES.items() for d in drugs
}

# Counts per stratum
COUNTS = {
    "S1": 20, "S2": 25, "S3": 40, "S4": 25,
    "S5": 25, "S6": 25, "S7": 15, "S8": 25,
}

HASH_KEYS = ("variant", "gene", "type", "level of evidence", "chemicals", "phenotypes")
def row_hash(row: dict) -> str:
    return "|".join((row.get(k) or "").strip() for k in HASH_KEYS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def split_field(v: str) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in re.split(r"[,;]", v) if x.strip()]


def first_drug_class(drugs: list[str]) -> str | None:
    for d in drugs:
        cls = DRUG_TO_CLASS.get(d.lower())
        if cls:
            return cls
    return None


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_pmids_for_pairs() -> dict[tuple[str, str], list[str]]:
    """Index relationships.tsv: (chem_name_lower, gene_symbol_upper) -> [pmids]"""
    idx: dict[tuple[str, str], list[str]] = defaultdict(list)
    if not RELATIONSHIPS.exists():
        return idx
    with RELATIONSHIPS.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for r in reader:
            assoc = (r.get("Association") or "").strip().lower()
            if assoc not in ("associated",):
                continue
            t1 = (r.get("Entity1_type") or "").strip().lower()
            t2 = (r.get("Entity2_type") or "").strip().lower()
            n1 = (r.get("Entity1_name") or "").strip()
            n2 = (r.get("Entity2_name") or "").strip()
            pmids = [p.strip() for p in re.split(r"[,;]", r.get("PMIDs") or "") if p.strip()]
            if not pmids:
                continue
            if t1 == "chemical" and t2 == "gene":
                idx[(n1.lower(), n2.upper())].extend(pmids)
            elif t1 == "gene" and t2 == "chemical":
                idx[(n2.lower(), n1.upper())].extend(pmids)
    # de-dup
    for k, v in idx.items():
        idx[k] = list(dict.fromkeys(v))
    return idx


def load_negative_pairs(limit: int) -> list[dict]:
    """Sample some 'not associated' / 'ambiguous' rows for negative controls."""
    out: list[dict] = []
    if not RELATIONSHIPS.exists():
        return out
    rng = random.Random(SEED + 100)
    cands: list[dict] = []
    with RELATIONSHIPS.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for r in reader:
            assoc = (r.get("Association") or "").strip().lower()
            if assoc not in ("not associated", "ambiguous"):
                continue
            t1 = (r.get("Entity1_type") or "").strip().lower()
            t2 = (r.get("Entity2_type") or "").strip().lower()
            if {t1, t2} != {"chemical", "gene"}:
                continue
            cands.append(r)
            if len(cands) >= 5000:
                break
    rng.shuffle(cands)
    return cands[:limit]


def load_ood_drugs(limit: int, in_pharmgkb_drugs: set[str]) -> list[str]:
    """Drugs not in PharmGKB at all — but we need a list of plausible drug names.

    Strategy: hand-curated list of medications that are well-known but
    typically not in PharmGKB clinical guidelines (vitamins, OTC, niche).
    """
    candidates = [
        "vitamin C", "melatonin", "glucosamine", "echinacea", "valerian root",
        "St John's wort", "ginkgo biloba", "milk thistle", "saw palmetto",
        "coenzyme Q10", "creatine", "alpha-lipoic acid", "lutein", "psyllium",
        "lactobacillus", "berberine", "ashwagandha", "rhodiola", "lysine",
        "DHEA", "selenium yeast", "spirulina", "chlorella", "bromelain",
    ]
    rng = random.Random(SEED + 200)
    rng.shuffle(candidates)
    return candidates[:limit]


def load_pharmgkb_drug_names() -> set[str]:
    """Lowercased set of drug names that appear in clinicalVariants chemicals column."""
    out: set[str] = set()
    if not FULL_CLIN.exists():
        return out
    with FULL_CLIN.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            for d in split_field((row.get("chemicals") or "").strip('"')):
                out.add(d.lower())
    return out


# ---------------------------------------------------------------------------
# Stratum builders — each returns list[dict] of {question, gold, template, source_row_hash}
# ---------------------------------------------------------------------------

def build_s1_wellknown(heldout: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Well-known facts: 'What gene is most strongly associated with {phenotype}?'

    Source: held-out rows that have a non-empty phenotype field and a
    well-known gene (we accept any). LLM probably knows this.
    """
    out = []
    cands = [r for r in heldout if (r.get("phenotypes") or "").strip()]
    rng.shuffle(cands)
    for r in cands:
        if len(out) >= n:
            break
        phen = split_field(r.get("phenotypes", "").strip('"'))
        if not phen:
            continue
        phen0 = phen[0]
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        if not gene:
            continue
        out.append({
            "stratum": "S1",
            "question": f"Which gene is most strongly associated with {phen0} in pharmacogenomic guidelines?",
            "gold": {
                "entities": [gene, phen0],
                "should_refuse": False,
                "answer_summary": f"{gene} is the most strongly associated gene.",
            },
            "template": "s1_phen_to_gene",
            "source_row_hash": row_hash(r),
        })
    return out


def build_s2_evidence_level(heldout: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Specific evidence level: 'What is the evidence level for {drug} × {gene} for {phenotype}?'"""
    out = []
    cands = list(heldout)
    rng.shuffle(cands)
    for r in cands:
        if len(out) >= n:
            break
        drugs = split_field((r.get("chemicals") or "").strip('"'))
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        level = (r.get("level of evidence") or "").strip()
        phen = split_field((r.get("phenotypes") or "").strip('"'))
        if not drugs or not gene or not level:
            continue
        drug0 = drugs[0]
        phen_part = f" for {phen[0]}" if phen else ""
        out.append({
            "stratum": "S2",
            "question": f"In PharmGKB's clinical guidelines, what is the evidence level for the {drug0} × {gene} association{phen_part}?",
            "gold": {
                "entities": [drug0, gene] + ([phen[0]] if phen else []),
                "evidence_level": level,
                "should_refuse": False,
                "answer_summary": f"Evidence level {level}.",
            },
            "template": "s2_evidence_level",
            "source_row_hash": row_hash(r),
        })
    return out


def build_s3_multihop(heldout: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Multi-hop: variant → gene → drug → class. Tests graph structure."""
    out = []
    cands = list(heldout)
    rng.shuffle(cands)
    for r in cands:
        if len(out) >= n:
            break
        variant = (r.get("variant") or "").strip().split(",")[0].strip()
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        drugs = split_field((r.get("chemicals") or "").strip('"'))
        if not variant or not gene or not drugs:
            continue
        cls = first_drug_class(drugs)
        if not cls:
            continue  # need a drug whose class is in our curated map
        cv_type = (r.get("type") or "").strip()
        is_toxic = "Toxicity" in cv_type
        action = "should be avoided" if is_toxic else "may require dose adjustment"
        out.append({
            "stratum": "S3",
            "question": (
                f"A patient is found to carry the {variant} variant in {gene}. "
                f"Which class of medications {action}, and why?"
            ),
            "gold": {
                "entities": [variant, gene, cls] + drugs[:3],
                "drug_class": cls,
                "should_refuse": False,
                "answer_summary": f"{cls} {action} because of the {gene} variant.",
            },
            "template": "s3_variant_to_class",
            "source_row_hash": row_hash(r),
        })
    return out


def build_s4_citation(heldout: list[dict], pmid_idx, rng: random.Random, n: int) -> list[dict]:
    """Citation grounding: 'Cite a PMID supporting the {drug} × {gene} association.'"""
    out = []
    cands = list(heldout)
    rng.shuffle(cands)
    for r in cands:
        if len(out) >= n:
            break
        drugs = split_field((r.get("chemicals") or "").strip('"'))
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        if not drugs or not gene:
            continue
        drug0 = drugs[0]
        pmids = pmid_idx.get((drug0.lower(), gene), [])
        if not pmids:
            continue
        out.append({
            "stratum": "S4",
            "question": (
                f"Cite a primary research reference (PMID) supporting the "
                f"{drug0} × {gene} pharmacogenomic association."
            ),
            "gold": {
                "entities": [drug0, gene],
                "pmids": pmids[:10],
                "should_refuse": False,
                "answer_summary": f"Any PMID from the curated list supporting {drug0} × {gene}.",
            },
            "template": "s4_citation",
            "source_row_hash": row_hash(r),
        })
    return out


def build_s5_longtail(heldout: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Long-tail / niche: pick rows where the drug or gene is uncommon."""
    # Use frequency in full clinicalVariants as a proxy for "well-known".
    drug_freq: dict[str, int] = defaultdict(int)
    gene_freq: dict[str, int] = defaultdict(int)
    if FULL_CLIN.exists():
        with FULL_CLIN.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                for d in split_field((r.get("chemicals") or "").strip('"')):
                    drug_freq[d.lower()] += 1
                for g in split_field((r.get("gene") or "").strip('"')):
                    gene_freq[g.upper()] += 1

    # Score each heldout row by min(drug_freq, gene_freq) — lower = more niche.
    scored = []
    for r in heldout:
        drugs = split_field((r.get("chemicals") or "").strip('"'))
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        if not drugs or not gene:
            continue
        s = min(drug_freq.get(drugs[0].lower(), 99), gene_freq.get(gene, 99))
        scored.append((s, r))
    scored.sort(key=lambda x: x[0])
    out = []
    for s, r in scored:
        if len(out) >= n:
            break
        drugs = split_field((r.get("chemicals") or "").strip('"'))
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        drug0 = drugs[0]
        out.append({
            "stratum": "S5",
            "question": (
                f"What pharmacogenomic relationship has PharmGKB documented "
                f"between {drug0} and {gene}? Be specific about the type "
                f"(metabolism/efficacy/toxicity)."
            ),
            "gold": {
                "entities": [drug0, gene],
                "evidence_level": (r.get("level of evidence") or "").strip(),
                "cv_type": (r.get("type") or "").strip(),
                "should_refuse": False,
                "answer_summary": f"PharmGKB documents a {(r.get('type') or 'pharmacogenomic')} relationship.",
            },
            "template": "s5_longtail_drug_gene",
            "source_row_hash": row_hash(r),
        })
    return out


def build_s6_negative(rng: random.Random, n: int) -> list[dict]:
    """Negative controls: drug-gene pairs flagged as NOT associated."""
    negs = load_negative_pairs(limit=max(n * 4, 200))
    rng.shuffle(negs)
    out = []
    for r in negs:
        if len(out) >= n:
            break
        t1 = (r.get("Entity1_type") or "").strip().lower()
        t2 = (r.get("Entity2_type") or "").strip().lower()
        if t1 == "chemical":
            drug, gene = (r.get("Entity1_name") or "").strip(), (r.get("Entity2_name") or "").strip().upper()
        else:
            drug, gene = (r.get("Entity2_name") or "").strip(), (r.get("Entity1_name") or "").strip().upper()
        if not drug or not gene:
            continue
        out.append({
            "stratum": "S6",
            "question": (
                f"According to PharmGKB-curated evidence, is there a clinically "
                f"meaningful pharmacogenomic association between {drug} and {gene}?"
            ),
            "gold": {
                "entities": [drug, gene],
                "should_refuse": False,   # answer "no association" is the correct response
                "expected_negative": True,
                "answer_summary": f"No, PharmGKB lists this pair as not associated / ambiguous.",
            },
            "template": "s6_negative_control",
            "source_row_hash": f"NEG|{drug.lower()}|{gene.upper()}",
        })
    return out


def build_s7_ood(rng: random.Random, n: int, pharmgkb_drugs: set[str]) -> list[dict]:
    drugs = load_ood_drugs(n, pharmgkb_drugs)
    out = []
    for d in drugs:
        out.append({
            "stratum": "S7",
            "question": (
                f"What pharmacogenomic guidelines exist for {d} in PharmGKB? "
                f"Cite the specific gene-drug interactions if any."
            ),
            "gold": {
                "entities": [d],
                "should_refuse": True,
                "answer_summary": (
                    f"{d} is not in PharmGKB's clinical guidelines; appropriate "
                    f"response is to indicate no pharmacogenomic guidelines exist."
                ),
            },
            "template": "s7_ood_drug",
            "source_row_hash": f"OOD|{d.lower()}",
        })
    return out


def build_s8_comparative(heldout: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Comparative: pick a held-out row whose drug has class-siblings with their own pgx data."""
    out = []
    # Build a map: (gene, drug_class) -> list of (drug, row, level)
    by_class: dict[tuple[str, str], list[tuple[str, dict, str]]] = defaultdict(list)
    sources = list(heldout)
    if FULL_CLIN.exists():
        with FULL_CLIN.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                if (r.get("level of evidence") or "").strip() not in {"1A", "1B", "2A", "2B"}:
                    continue
                sources.append(r)
    for r in sources:
        gene = (r.get("gene") or "").strip().split(",")[0].strip().upper()
        level = (r.get("level of evidence") or "").strip()
        for d in split_field((r.get("chemicals") or "").strip('"')):
            cls = DRUG_TO_CLASS.get(d.lower())
            if cls and gene:
                by_class[(gene, cls)].append((d, r, level))

    candidates = []
    for (gene, cls), members in by_class.items():
        unique_drugs = list({m[0].lower(): m for m in members}.values())
        if len(unique_drugs) >= 2:
            candidates.append((gene, cls, unique_drugs))
    rng.shuffle(candidates)
    for gene, cls, members in candidates:
        if len(out) >= n:
            break
        # pick 2 drugs from the class
        a, b = members[0], members[1]
        drug_a, drug_b = a[0], b[0]
        level_a, level_b = a[2], b[2]
        out.append({
            "stratum": "S8",
            "question": (
                f"Between {drug_a} and {drug_b} (both in the {cls} class), "
                f"which has stronger PharmGKB evidence for an interaction with {gene}, "
                f"and at what evidence level?"
            ),
            "gold": {
                "entities": [drug_a, drug_b, cls, gene],
                "evidence_levels": {drug_a: level_a, drug_b: level_b},
                "should_refuse": False,
                "answer_summary": f"{drug_a}={level_a}, {drug_b}={level_b}.",
            },
            "template": "s8_comparative_same_class",
            "source_row_hash": f"CMP|{gene}|{cls}|{drug_a}|{drug_b}",
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not HELDOUT.exists():
        print("missing eval/heldout_variants.tsv — run eval/rebuild_heldout.py first", file=sys.stderr)
        return 1

    heldout = load_rows(HELDOUT)
    print(f"loaded {len(heldout)} held-out rows", file=sys.stderr)
    pmid_idx = load_pmids_for_pairs()
    print(f"indexed {len(pmid_idx)} (drug, gene) → PMID lists", file=sys.stderr)
    pharmgkb_drugs = load_pharmgkb_drug_names()
    print(f"indexed {len(pharmgkb_drugs)} PharmGKB drug names", file=sys.stderr)

    rng = random.Random(SEED)
    questions: list[dict] = []
    questions += build_s1_wellknown(heldout, rng, COUNTS["S1"])
    questions += build_s2_evidence_level(heldout, rng, COUNTS["S2"])
    questions += build_s3_multihop(heldout, rng, COUNTS["S3"])
    questions += build_s4_citation(heldout, pmid_idx, rng, COUNTS["S4"])
    questions += build_s5_longtail(heldout, rng, COUNTS["S5"])
    questions += build_s6_negative(rng, COUNTS["S6"])
    questions += build_s7_ood(rng, COUNTS["S7"], pharmgkb_drugs)
    questions += build_s8_comparative(heldout, rng, COUNTS["S8"])

    # Assign stable IDs S{n}-{idx:03d}
    by_stratum: dict[str, int] = defaultdict(int)
    for q in questions:
        by_stratum[q["stratum"]] += 1
        q["id"] = f"{q['stratum']}-{by_stratum[q['stratum']]:03d}"

    with OUT.open("w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")

    counts = defaultdict(int)
    for q in questions:
        counts[q["stratum"]] += 1
    print(f"wrote {len(questions)} questions to {OUT}", file=sys.stderr)
    for s in sorted(counts):
        target = COUNTS.get(s, "?")
        print(f"  {s}: {counts[s]} (target {target})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
