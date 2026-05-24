// Server-side graph loader. The preprocessor writes JSON files to
// ../data/ relative to the web app; we read them once and cache in memory.

import fs from "node:fs";
import path from "node:path";
import type { Graph, SearchHit, GraphNode, GraphEdge } from "./types";

const DATA_DIR = path.resolve(process.cwd(), "..", "data");

let _full: Graph | null = null;
let _seed: Graph | null = null;
let _index: SearchHit[] | null = null;

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
  if (!_index) _index = readJson<SearchHit[]>("search_index.json");
  return _index;
}

// ---------------------------------------------------------------------------
// Retrieval helpers — used by /api/graph (expand) and /api/explain (subgraph).
// ---------------------------------------------------------------------------

export function neighbourhood(
  graph: Graph,
  seeds: string[],
  hops: number = 1
): Graph {
  const keep = new Set(seeds);
  const adj = new Map<string, GraphEdge[]>();
  for (const e of graph.edges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    if (!adj.has(e.target)) adj.set(e.target, []);
    adj.get(e.source)!.push(e);
    adj.get(e.target)!.push(e);
  }
  let frontier = new Set(seeds);
  for (let i = 0; i < hops; i++) {
    const next = new Set<string>();
    for (const nid of frontier) {
      for (const e of adj.get(nid) ?? []) {
        const other = e.source === nid ? e.target : e.source;
        if (!keep.has(other)) {
          keep.add(other);
          next.add(other);
        }
      }
    }
    frontier = next;
    if (frontier.size === 0) break;
  }
  const nodeMap = new Map(graph.nodes.map((n) => [n.id, n]));
  const nodes: GraphNode[] = [];
  for (const id of keep) {
    const n = nodeMap.get(id);
    if (n) nodes.push(n);
  }
  const edges = graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target));
  return { nodes, edges };
}

export interface FilterOptions {
  nodeTypes?: string[];
  edgeTypes?: string[];
  evidenceLevels?: string[];  // "1A", "1B", ...
  hideAmbiguous?: boolean;    // (legacy — we already dropped these)
  maxNodes?: number;
}

export function applyFilters(graph: Graph, opts: FilterOptions): Graph {
  let nodes = graph.nodes;
  let edges = graph.edges;

  if (opts.nodeTypes?.length) {
    const set = new Set(opts.nodeTypes);
    nodes = nodes.filter((n) => set.has(n.type));
  }
  if (opts.edgeTypes?.length) {
    const set = new Set(opts.edgeTypes);
    edges = edges.filter((e) => set.has(e.type));
  }
  if (opts.evidenceLevels?.length) {
    const set = new Set(opts.evidenceLevels);
    edges = edges.filter((e) => !e.level || set.has(e.level));
  }
  const nodeIds = new Set(nodes.map((n) => n.id));
  edges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

  if (opts.maxNodes && nodes.length > opts.maxNodes) {
    // keep the highest-degree nodes — usually the interesting hubs
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    nodes = [...nodes].sort(
      (a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0)
    ).slice(0, opts.maxNodes);
    const keep = new Set(nodes.map((n) => n.id));
    edges = edges.filter((e) => keep.has(e.source) && keep.has(e.target));
  }
  return { nodes, edges };
}
