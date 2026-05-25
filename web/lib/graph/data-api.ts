"use client";

// Thin facade for the graph/search endpoints. When the app is built for
// Firebase (NEXT_PUBLIC_BYOK=1) there are no API routes, so we fall back to
// fetching the static /data/*.json files and running the operations in-browser.
// Locally (server build) we call the API routes exactly as before.

import type { Graph, SearchHit } from "./types";
import { neighbourhood, applyFilters } from "./ops";
import {
  loadFullGraph,
  loadSeedGraph,
  loadSearchIndex,
} from "./client-loader";

const BYOK = process.env.NEXT_PUBLIC_BYOK === "1";

export async function fetchSeedGraph(): Promise<Graph> {
  if (BYOK) return loadSeedGraph();
  const r = await fetch("/api/graph?view=seed");
  return r.json();
}

export async function fetchGraphForSeed(
  seed: string,
  hops = 2,
  maxNodes = 120
): Promise<Graph> {
  if (BYOK) {
    const full = await loadFullGraph();
    const sub = neighbourhood(full, [seed], Math.min(Math.max(hops, 1), 3));
    return applyFilters(sub, { maxNodes });
  }
  const r = await fetch(
    `/api/graph?seed=${encodeURIComponent(seed)}&hops=${hops}&maxNodes=${maxNodes}`
  );
  return r.json();
}

export async function fetchSearchIndex(): Promise<SearchHit[]> {
  if (BYOK) return loadSearchIndex();
  const r = await fetch("/api/search");
  return r.json();
}
