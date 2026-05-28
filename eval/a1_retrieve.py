#!/usr/bin/env python3
"""
A1 retrieve: given a question, return top-8 chunks via BM25 + dense
embeddings, fused with reciprocal-rank-fusion (RRF, k=60).

Loaded once at module import; reused across calls in run.py.
"""

from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "eval" / ".cache"

EMBEDDER_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K = 20
TOP_FINAL = 8
RRF_K = 60


def _load():
    chunks = json.loads((CACHE / "a1_chunks.json").read_text())
    with (CACHE / "a1_bm25.pkl").open("rb") as fh:
        bm25 = pickle.load(fh)
    embs = np.load(CACHE / "a1_embeddings.npy")
    model = SentenceTransformer(EMBEDDER_NAME)
    return chunks, bm25, embs, model


_CHUNKS, _BM25, _EMBS, _MODEL = _load()


def retrieve(query: str, top_final: int = TOP_FINAL) -> list[dict]:
    """Return top chunks as list of dicts. Each chunk has 'text', 'source', + metadata."""
    qt = re.findall(r"\w+", query.lower())
    bm25_scores = _BM25.get_scores(qt)
    # rank: highest score → lowest. Take top TOP_K
    bm25_top = np.argsort(-bm25_scores)[:TOP_K]
    qvec = _MODEL.encode([query], normalize_embeddings=True)[0].astype(np.float32)
    dense_scores = _EMBS @ qvec
    dense_top = np.argsort(-dense_scores)[:TOP_K]

    # RRF: sum 1/(k + rank) across lists
    rrf: dict[int, float] = {}
    for rank, idx in enumerate(bm25_top):
        rrf[int(idx)] = rrf.get(int(idx), 0.0) + 1.0 / (RRF_K + rank)
    for rank, idx in enumerate(dense_top):
        rrf[int(idx)] = rrf.get(int(idx), 0.0) + 1.0 / (RRF_K + rank)

    ranked = sorted(rrf.items(), key=lambda x: -x[1])[:top_final]
    return [_CHUNKS[idx] for idx, _ in ranked]


def render_a1_context(chunks: list[dict]) -> str:
    lines = ["RETRIEVED EVIDENCE (top-8 by hybrid BM25 + dense search):"]
    for i, c in enumerate(chunks, 1):
        lines.append(f"  [{i}] {c['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Why is sevoflurane risky for malignant hyperthermia?"
    print(f"--- query: {q}")
    res = retrieve(q)
    for r in res:
        print(f"  · {r['text'][:120]}")
