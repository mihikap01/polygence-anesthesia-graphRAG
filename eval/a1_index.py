#!/usr/bin/env python3
"""
A1 (steelman naïve RAG) index — builds one-time, queried by run.py.

Chunks: each high-evidence clinicalVariants row (held-out rows EXCLUDED),
denormalised so entity names appear in chunk text. Plus relationships.tsv
"Associated" rows with PMIDs.

Index: BM25 + dense (sentence-transformers all-MiniLM-L6-v2), fused per
query via reciprocal-rank-fusion (RRF, k=60). Top-K=20 → top-8 in prompt.

DEVIATION FROM PRE-REGISTRATION: the original spec called for a BGE
reranker (BAAI/bge-reranker-v2-m3, ~600MB). We deferred this to keep the
laptop install lighter; BM25+dense via RRF is still a strong steelman.
Documented in the final report's "deviations" section.

Caches everything to eval/.cache/ so the index builds once.
"""

from __future__ import annotations

import csv
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
CACHE = EVAL / ".cache"
CLIN = ROOT / "clinicalVariants.tsv"
RELS = ROOT / "relationships.tsv"
HELDOUT_HASHES = EVAL / "heldout_variant_hashes.txt"

EMBEDDER_NAME = "sentence-transformers/all-MiniLM-L6-v2"

HASH_KEYS = ("variant", "gene", "type", "level of evidence", "chemicals", "phenotypes")
def row_hash(row: dict) -> str:
    return "|".join((row.get(k) or "").strip() for k in HASH_KEYS)


def chunk_clin_row(row: dict) -> dict | None:
    """Render a clinicalVariants row as a self-contained chunk."""
    variant = (row.get("variant") or "").strip()
    gene = (row.get("gene") or "").strip()
    cv_type = (row.get("type") or "").strip()
    level = (row.get("level of evidence") or "").strip()
    chems = (row.get("chemicals") or "").strip()
    phens = (row.get("phenotypes") or "").strip()
    if not (variant and gene and chems):
        return None
    text = (
        f"PharmGKB clinical variant — gene: {gene}; variant(s): {variant}; "
        f"type: {cv_type}; evidence level: {level}; "
        f"chemicals: {chems}"
    )
    if phens:
        text += f"; phenotype(s): {phens}"
    return {
        "source": "clinicalVariants",
        "text": text,
        "gene": gene, "variant": variant, "level": level,
        "chemicals": chems, "phenotypes": phens, "cv_type": cv_type,
    }


def chunk_rels_row(row: dict) -> dict | None:
    """Render a relationships.tsv 'Associated' row with PMIDs."""
    if (row.get("Association") or "").strip().lower() != "associated":
        return None
    pmids = [p.strip() for p in re.split(r"[,;]", row.get("PMIDs") or "") if p.strip()]
    if not pmids:
        return None
    n1 = (row.get("Entity1_name") or "").strip()
    t1 = (row.get("Entity1_type") or "").strip()
    n2 = (row.get("Entity2_name") or "").strip()
    t2 = (row.get("Entity2_type") or "").strip()
    if not n1 or not n2:
        return None
    text = (
        f"PharmGKB relationship: {n1} ({t1}) — {n2} ({t2}); "
        f"PMIDs: {', '.join(pmids[:10])}"
    )
    return {
        "source": "relationships", "text": text,
        "entity1": n1, "entity2": n2, "pmids": pmids[:10],
    }


def build_chunks() -> list[dict]:
    held = set()
    if HELDOUT_HASHES.exists():
        held = {ln.strip() for ln in HELDOUT_HASHES.read_text().splitlines() if ln.strip()}
    chunks: list[dict] = []

    csv.field_size_limit(sys.maxsize)
    with CLIN.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        kept = dropped = 0
        for row in reader:
            level = (row.get("level of evidence") or "").strip()
            # Only index high-evidence rows for A1 — same evidence floor as A3's graph
            if level not in ("1A", "1B", "2A", "2B"):
                continue
            if row_hash(row) in held:
                dropped += 1
                continue
            c = chunk_clin_row(row)
            if c:
                chunks.append(c); kept += 1
    print(f"clinicalVariants: kept {kept} chunks, dropped {dropped} held-out", file=sys.stderr)

    with RELS.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        kept = 0
        for row in reader:
            c = chunk_rels_row(row)
            if c:
                chunks.append(c); kept += 1
                if kept >= 20000:  # keep relationships index manageable
                    break
    print(f"relationships: kept {kept} chunks", file=sys.stderr)
    return chunks


def main() -> int:
    CACHE.mkdir(exist_ok=True)
    cache_chunks = CACHE / "a1_chunks.json"
    cache_bm25 = CACHE / "a1_bm25.pkl"
    cache_emb = CACHE / "a1_embeddings.npy"

    if cache_chunks.exists() and cache_bm25.exists() and cache_emb.exists():
        print(f"cache exists ({cache_chunks}); delete eval/.cache/ to rebuild", file=sys.stderr)
        return 0

    print("building chunks...", file=sys.stderr)
    chunks = build_chunks()
    cache_chunks.write_text(json.dumps(chunks))
    print(f"total: {len(chunks)} chunks", file=sys.stderr)

    print("building BM25 index...", file=sys.stderr)
    tokenized = [re.findall(r"\w+", c["text"].lower()) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    with cache_bm25.open("wb") as fh:
        pickle.dump(bm25, fh)

    print(f"embedding with {EMBEDDER_NAME}... (this can take a few minutes)", file=sys.stderr)
    model = SentenceTransformer(EMBEDDER_NAME)
    texts = [c["text"] for c in chunks]
    embs = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    np.save(cache_emb, embs.astype(np.float32))

    print(f"done. cached to {CACHE}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
