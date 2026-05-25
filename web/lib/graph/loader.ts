// Server-side graph loader. The preprocessor writes JSON files to
// ../data/ relative to the web app; we read them once and cache in memory.
//
// NOTE: imports `fs` — do NOT import this file from client components.
// Browser code should use `client-loader.ts` instead, which fetches over HTTP.

import fs from "node:fs";
import path from "node:path";
import type { Graph, SearchHit } from "./types";
import {
  setSearchIndex,
  getSearchIndex as runtimeGetSearchIndex,
  hasSearchIndex,
} from "./runtime";

// Re-export pure helpers for back-compat with existing API-route imports.
export { neighbourhood, applyFilters } from "./ops";
export type { FilterOptions } from "./ops";

const DATA_DIR = path.resolve(process.cwd(), "..", "data");

let _full: Graph | null = null;
let _seed: Graph | null = null;

function readJson<T>(name: string): T {
  const p = path.join(DATA_DIR, name);
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

export function getFullGraph(): Graph {
  if (!_full) _full = readJson<Graph>("graph.json");
  return _full;
}

export function getSeedGraph(): Graph {
  if (!_seed) _seed = readJson<Graph>("seed_anesthesia.json");
  return _seed;
}

export function getSearchIndex(): SearchHit[] {
  if (!hasSearchIndex()) {
    setSearchIndex(readJson<SearchHit[]>("search_index.json"));
  }
  return runtimeGetSearchIndex();
}
