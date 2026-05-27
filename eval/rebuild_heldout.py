#!/usr/bin/env python3
"""
Deterministic held-out split for the GraphRAG eval.

Selects ~30% of the high-evidence (1A/1B/2A/2B) clinicalVariants.tsv rows as
"held-out", writes their stable hashes to eval/heldout_variant_hashes.txt,
also writes the full held-out rows to eval/heldout_variants.tsv (so the
question generator can read them later), then rebuilds the graph WITHOUT
those rows by invoking preprocess/build_graph.py with HELDOUT_VARIANTS set.

Output:
  eval/heldout_variant_hashes.txt   — one hash per line
  eval/heldout_variants.tsv         — full held-out rows
  data/graph_heldout.json           — reduced graph (no held-out rows)

Determinism: HELDOUT_SEED env var controls the random sample (default 42).
"""

from __future__ import annotations

import csv
import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
CLIN = ROOT / "clinicalVariants.tsv"

HIGH_EV = {"1A", "1B", "2A", "2B"}
HOLDOUT_FRAC = float(os.environ.get("HELDOUT_FRAC", "0.30"))
SEED = int(os.environ.get("HELDOUT_SEED", "42"))

HASH_KEYS = ("variant", "gene", "type", "level of evidence", "chemicals", "phenotypes")


def row_hash(row: dict) -> str:
    return "|".join((row.get(k) or "").strip() for k in HASH_KEYS)


def main() -> int:
    if not CLIN.exists():
        print(f"missing {CLIN}", file=sys.stderr)
        return 1
    EVAL.mkdir(exist_ok=True)

    with CLIN.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)

    high = [r for r in all_rows if (r.get("level of evidence") or "").strip() in HIGH_EV]
    print(
        f"clinicalVariants: {len(all_rows)} total, {len(high)} high-evidence "
        f"(1A/1B/2A/2B)",
        file=sys.stderr,
    )

    rng = random.Random(SEED)
    n_holdout = round(len(high) * HOLDOUT_FRAC)
    holdout = rng.sample(high, n_holdout)
    print(f"held-out: {n_holdout} rows ({HOLDOUT_FRAC:.0%}, seed={SEED})", file=sys.stderr)

    holdout_hashes = [row_hash(r) for r in holdout]
    (EVAL / "heldout_variant_hashes.txt").write_text("\n".join(holdout_hashes) + "\n")
    with (EVAL / "heldout_variants.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        w.writeheader()
        w.writerows(holdout)
    print(
        f"wrote eval/heldout_variant_hashes.txt + eval/heldout_variants.tsv",
        file=sys.stderr,
    )

    env = {
        **os.environ,
        "HELDOUT_VARIANTS": str(EVAL / "heldout_variant_hashes.txt"),
        "HELDOUT_OUTPUT": "graph_heldout.json",
    }
    print("rebuilding graph without held-out rows...", file=sys.stderr)
    r = subprocess.run(
        [sys.executable, str(ROOT / "preprocess" / "build_graph.py")],
        env=env,
        cwd=ROOT,
    )
    if r.returncode != 0:
        return r.returncode

    print("done.", file=sys.stderr)
    print(f"  reduced graph: data/graph_heldout.json", file=sys.stderr)
    print(f"  held-out rows: eval/heldout_variants.tsv", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
