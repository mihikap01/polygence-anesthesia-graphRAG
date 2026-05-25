"use client";

// Browser-side graph loader for the static (BYOK) build.
// Fetches /data/*.json once, caches in memory, primes the runtime store
// so retrieve.ts works without any server route.

import type { Graph, SearchHit } from "./types";
import { setSearchIndex, hasSearchIndex } from "./runtime";

let _full: Promise<Graph> | null = null;
let _seed: Promise<Graph> | null = null;
let _index: Promise<SearchHit[]> | null = null;

async function fetchJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch ${url} → ${r.status}`);
  return (await r.json()) as T;
}

export function loadFullGraph(): Promise<Graph> {
  if (!_full) _full = fetchJson<Graph>("/data/graph.json");
  return _full;
}

export function loadSeedGraph(): Promise<Graph> {
  if (!_seed) _seed = fetchJson<Graph>("/data/seed_anesthesia.json");
  return _seed;
}

export function loadSearchIndex(): Promise<SearchHit[]> {
  if (!_index) {
    _index = fetchJson<SearchHit[]>("/data/search_index.json").then((idx) => {
      if (!hasSearchIndex()) setSearchIndex(idx);
      return idx;
    });
  }
  return _index;
}

/** Convenience: prime everything retrieval needs in one await. */
export async function ensureGraphLoaded(): Promise<{
  full: Graph;
  index: SearchHit[];
}> {
  const [full, index] = await Promise.all([loadFullGraph(), loadSearchIndex()]);
  return { full, index };
}
