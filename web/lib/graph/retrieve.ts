// Retrieval / Context agent.
//
// Builds compact, graph-grounded "context packets" that the explanation and
// chat agents pass to the LLM.  The packet is plain text — much cheaper for
// the model than raw JSON and produces noticeably better answers.

import Fuse from "fuse.js";
import type { Graph, GraphEdge, GraphNode, SearchHit } from "./types";
import { neighbourhood } from "./ops";
import { getSearchIndex } from "./runtime";

const EDGE_VERB: Record<string, string> = {
  linked_to_risk: "is linked to risk of",
  affects_response_to: "affects response to",
  can_trigger: "can trigger",
  has_variant: "has variant cluster",
  belongs_to_class: "belongs to drug class",
  associated_with: "is associated with",
};

export interface ContextPacket {
  text: string;
  pmids: string[];
  subgraph: Graph;
  focus?: GraphNode;
  focusEdge?: GraphEdge;
}

export function nodeContext(graph: Graph, nodeId: string, hops = 1): ContextPacket {
  const sub = neighbourhood(graph, [nodeId], hops);
  const focus = sub.nodes.find((n) => n.id === nodeId);
  const lines: string[] = [];
  const pmids = new Set<string>();

  if (focus) {
    lines.push(`Focus node: ${focus.label} (${focus.type}${focus.fullName ? `, "${focus.fullName}"` : ""})`);
    if (focus.chromosome) lines.push(`  chromosome: ${focus.chromosome}`);
    if (focus.atc) lines.push(`  ATC codes: ${focus.atc}`);
    if (focus.members?.length) {
      lines.push(`  variants (${focus.members.length}): ${focus.members.slice(0, 8).map(m => m.rsid).join(", ")}${focus.members.length > 8 ? ", …" : ""}`);
    }
  }
  lines.push("");
  lines.push("Neighbouring relationships:");
  const byId = new Map(sub.nodes.map((n) => [n.id, n]));
  for (const e of sub.edges) {
    if (e.source !== nodeId && e.target !== nodeId) continue;
    const a = byId.get(e.source);
    const b = byId.get(e.target);
    if (!a || !b) continue;
    const verb = EDGE_VERB[e.type] ?? e.type;
    const tag: string[] = [];
    if (e.level) tag.push(`evidence ${e.level}`);
    if (e.role) tag.push(e.role);
    if (e.critical) tag.push("CRITICAL");
    if (e.count) tag.push(`${e.count} variants`);
    lines.push(`  - ${a.label} (${a.type}) ${verb} ${b.label} (${b.type})${tag.length ? ` [${tag.join(", ")}]` : ""}`);
    for (const p of e.pmids ?? []) pmids.add(p);
  }
  // second-hop summary for context if hops>=2
  if (hops >= 2) {
    lines.push("");
    lines.push("Second-hop context:");
    for (const e of sub.edges) {
      if (e.source === nodeId || e.target === nodeId) continue;
      const a = byId.get(e.source);
      const b = byId.get(e.target);
      if (!a || !b) continue;
      lines.push(`  - ${a.label} ${EDGE_VERB[e.type] ?? e.type} ${b.label}`);
      for (const p of e.pmids ?? []) pmids.add(p);
    }
  }
  return { text: lines.join("\n"), pmids: [...pmids], subgraph: sub, focus };
}

export function edgeContext(graph: Graph, edge: GraphEdge): ContextPacket {
  const sub = neighbourhood(graph, [edge.source, edge.target], 1);
  const byId = new Map(sub.nodes.map((n) => [n.id, n]));
  const a = byId.get(edge.source);
  const b = byId.get(edge.target);
  const lines: string[] = [];
  if (a && b) {
    const verb = EDGE_VERB[edge.type] ?? edge.type;
    lines.push(`Focus edge: ${a.label} (${a.type}) ${verb} ${b.label} (${b.type})`);
    if (edge.level) lines.push(`  evidence level: ${edge.level}`);
    if (edge.role) lines.push(`  role: ${edge.role}`);
    if (edge.critical) lines.push(`  flagged: CRITICAL pharmacogenomic risk`);
    if (edge.gene) lines.push(`  mediating gene(s): ${edge.gene}`);
    lines.push("");
    lines.push(`Surrounding context for ${a.label}:`);
    for (const e of sub.edges) {
      if (e.id === edge.id) continue;
      if (e.source !== a.id && e.target !== a.id) continue;
      const o = byId.get(e.source === a.id ? e.target : e.source);
      if (!o) continue;
      lines.push(`  - ${a.label} ${EDGE_VERB[e.type] ?? e.type} ${o.label}${e.level ? ` (${e.level})` : ""}`);
    }
    lines.push(`Surrounding context for ${b.label}:`);
    for (const e of sub.edges) {
      if (e.id === edge.id) continue;
      if (e.source !== b.id && e.target !== b.id) continue;
      const o = byId.get(e.source === b.id ? e.target : e.source);
      if (!o) continue;
      lines.push(`  - ${b.label} ${EDGE_VERB[e.type] ?? e.type} ${o.label}${e.level ? ` (${e.level})` : ""}`);
    }
  }
  const pmids = new Set<string>();
  for (const p of edge.pmids ?? []) pmids.add(p);
  return { text: lines.join("\n"), pmids: [...pmids], subgraph: sub, focusEdge: edge };
}

// ---------------------------------------------------------------------------
// Question-driven retrieval — the actual GraphRAG step.
//
// 1. Entity linking: extract the question's n-grams, fuzzy-match against the
//    search index, and pick the best graph nodes.  Cheap & language-agnostic
//    enough for a 3k-node domain.
// 2. Per-entity 1-hop neighbourhoods.
// 3. Shortest paths between every pair of mentioned entities (BFS up to
//    `maxPathHops` hops, weighting critical edges as preferred).
// 4. Render everything as structured text the LLM can quote from.
// ---------------------------------------------------------------------------

const STOPWORDS = new Set([
  "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
  "of", "to", "for", "in", "on", "at", "by", "with", "from", "and", "or",
  "but", "if", "then", "than", "so", "as", "it", "this", "that", "these",
  "those", "what", "which", "who", "whom", "whose", "where", "when",
  "why", "how", "does", "do", "did", "can", "could", "should", "would",
  "will", "have", "has", "had", "i", "you", "we", "they", "he", "she",
  "me", "us", "them", "him", "her", "between", "about", "into", "through",
  "during", "before", "after", "above", "below", "tell", "explain", "show",
  "list", "give", "find", "say", "see", "look", "describe", "any", "some",
  "all", "most", "more", "less", "very",
]);

let _fuse: Fuse<SearchHit> | null = null;
function getFuse(): Fuse<SearchHit> {
  if (_fuse) return _fuse;
  _fuse = new Fuse(getSearchIndex(), {
    keys: ["label", "alt"],
    // Tight threshold — biomedical entity names are distinctive, so favour
    // precision over recall.  Verbs like "connect" should never match a gene.
    threshold: 0.15,
    distance: 60,
    includeScore: true,
    ignoreLocation: true,
    minMatchCharLength: 3,
  });
  return _fuse;
}

export interface LinkedEntity {
  id: string;
  label: string;
  type: GraphNode["type"];
  matchedTerm: string;
  score: number;     // 0 = perfect, 1 = no match
}

/** Pull plausible n-grams (1–3 tokens) from the question, filter stopwords. */
function questionNgrams(q: string): string[] {
  const tokens = q
    .toLowerCase()
    .replace(/[^\w\s-]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length >= 3 && !STOPWORDS.has(t));
  const grams: string[] = [];
  for (let n = 3; n >= 1; n--) {
    for (let i = 0; i + n <= tokens.length; i++) {
      grams.push(tokens.slice(i, i + n).join(" "));
    }
  }
  return [...new Set(grams)];
}

export function extractEntities(graph: Graph, question: string, opts: { maxEntities?: number } = {}): LinkedEntity[] {
  const maxEntities = opts.maxEntities ?? 6;
  const fuse = getFuse();
  const nodeIds = new Set(graph.nodes.map((n) => n.id));
  const seen = new Map<string, LinkedEntity>();

  for (const term of questionNgrams(question)) {
    // Single-token terms must be reasonably specific (≥4 chars or contains a
    // digit, like "RYR1") — otherwise we get noise from "connect", "risky"…
    const isMultiWord = term.includes(" ");
    if (!isMultiWord && term.length < 4 && !/\d/.test(term)) continue;

    const wordBonus = isMultiWord ? -0.05 : 0;
    const hits = fuse.search(term).slice(0, 3);
    for (const h of hits) {
      if (!nodeIds.has(h.item.id)) continue;
      const score = (h.score ?? 1) + wordBonus;
      // post-filter — even with a tight Fuse threshold, drop weak matches.
      if (score > 0.18) continue;
      const existing = seen.get(h.item.id);
      if (!existing || score < existing.score) {
        seen.set(h.item.id, {
          id: h.item.id, label: h.item.label, type: h.item.type as GraphNode["type"],
          matchedTerm: term, score,
        });
      }
    }
  }

  return [...seen.values()]
    .sort((a, b) => a.score - b.score)
    .slice(0, maxEntities);
}

export interface GraphPath {
  fromId: string;
  toId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];     // length = nodes.length - 1
  hops: number;
  critical: boolean;      // any edge in the path is critical
}

/** Shortest undirected path between two nodes via BFS, capped at `maxHops`. */
export function shortestPath(graph: Graph, fromId: string, toId: string, maxHops = 4): GraphPath | null {
  if (fromId === toId) return null;
  const adj = new Map<string, GraphEdge[]>();
  for (const e of graph.edges) {
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e);
  }
  const parent = new Map<string, { node: string; edge: GraphEdge } | null>();
  parent.set(fromId, null);
  const queue: Array<[string, number]> = [[fromId, 0]];
  while (queue.length) {
    const [n, d] = queue.shift()!;
    if (n === toId) break;
    if (d >= maxHops) continue;
    for (const e of adj.get(n) ?? []) {
      const other = e.source === n ? e.target : e.source;
      if (parent.has(other)) continue;
      parent.set(other, { node: n, edge: e });
      queue.push([other, d + 1]);
    }
  }
  if (!parent.has(toId)) return null;

  // reconstruct
  const nodeMap = new Map(graph.nodes.map((n) => [n.id, n]));
  const nodeIds: string[] = [];
  const edges: GraphEdge[] = [];
  let cur: string | null = toId;
  while (cur) {
    nodeIds.push(cur);
    const p = parent.get(cur);
    if (!p) break;
    edges.push(p.edge);
    cur = p.node;
  }
  nodeIds.reverse();
  edges.reverse();
  const nodes = nodeIds.map((id) => nodeMap.get(id)).filter(Boolean) as GraphNode[];
  return {
    fromId, toId, nodes, edges,
    hops: edges.length,
    critical: edges.some((e) => e.critical || e.level === "1A" || e.level === "1B"),
  };
}

export interface QuestionRetrieval {
  question: string;
  entities: LinkedEntity[];
  neighborhoods: Array<{ entityId: string; graph: Graph }>;
  paths: GraphPath[];
  contextText: string;
  pmids: string[];
  subgraph: Graph;          // union of all retrieved nodes/edges
}

function renderEdge(a: GraphNode, e: GraphEdge, b: GraphNode): string {
  const verb = EDGE_VERB[e.type] ?? e.type;
  const tag: string[] = [];
  if (e.level) tag.push(`L${e.level}`);
  if (e.role) tag.push(e.role);
  if (e.critical) tag.push("CRITICAL");
  if (e.count) tag.push(`${e.count} variants`);
  return `${a.label} --${verb}${tag.length ? `[${tag.join(", ")}]` : ""}--> ${b.label}`;
}

export function retrieveForQuestion(
  graph: Graph,
  question: string,
  opts: { focusNodeId?: string | null; visibleNodeIds?: string[]; maxPathHops?: number } = {}
): QuestionRetrieval {
  const entities = extractEntities(graph, question);
  // Always include the focused node as an "entity of interest" if not already linked
  if (opts.focusNodeId && !entities.some((e) => e.id === opts.focusNodeId)) {
    const f = graph.nodes.find((n) => n.id === opts.focusNodeId);
    if (f) {
      entities.unshift({
        id: f.id, label: f.label, type: f.type,
        matchedTerm: "(currently selected)", score: 0,
      });
    }
  }

  const nodeMap = new Map(graph.nodes.map((n) => [n.id, n]));
  const visibleSet = new Set(opts.visibleNodeIds ?? []);
  const pmids = new Set<string>();
  const lines: string[] = [];
  lines.push(`QUESTION: ${question}`);
  lines.push("");

  if (entities.length === 0) {
    lines.push("ENTITY LINKING: no specific graph entities matched the question.");
    lines.push("");
    if (visibleSet.size) {
      lines.push(`Currently visible to the user: ${[...visibleSet].slice(0, 12)
        .map((id) => nodeMap.get(id)?.label).filter(Boolean).join(", ")}`);
    }
    return {
      question, entities: [], neighborhoods: [], paths: [],
      contextText: lines.join("\n"),
      pmids: [],
      subgraph: { nodes: [], edges: [] },
    };
  }

  lines.push("ENTITIES IDENTIFIED IN QUESTION:");
  for (const e of entities) {
    lines.push(`  • ${e.label} (${e.type}) — matched "${e.matchedTerm}"`);
  }
  lines.push("");

  // 1-hop neighbourhoods per entity
  const neighborhoods: Array<{ entityId: string; graph: Graph }> = [];
  const unionNodes = new Map<string, GraphNode>();
  const unionEdges = new Map<string, GraphEdge>();

  for (const ent of entities) {
    const sub = neighbourhood(graph, [ent.id], 1);
    neighborhoods.push({ entityId: ent.id, graph: sub });
    for (const n of sub.nodes) unionNodes.set(n.id, n);
    for (const e of sub.edges) unionEdges.set(e.id, e);

    lines.push(`DIRECT NEIGHBOURHOOD OF ${ent.label}:`);
    for (const e of sub.edges) {
      if (e.source !== ent.id && e.target !== ent.id) continue;
      const a = nodeMap.get(e.source);
      const b = nodeMap.get(e.target);
      if (!a || !b) continue;
      lines.push(`  - ${renderEdge(a, e, b)}`);
      for (const p of e.pmids ?? []) pmids.add(p);
    }
    lines.push("");
  }

  // Shortest paths between every pair of entities
  const paths: GraphPath[] = [];
  const maxHops = opts.maxPathHops ?? 4;
  for (let i = 0; i < entities.length; i++) {
    for (let j = i + 1; j < entities.length; j++) {
      const p = shortestPath(graph, entities[i].id, entities[j].id, maxHops);
      if (p) paths.push(p);
    }
  }
  if (paths.length > 0) {
    paths.sort((a, b) => Number(b.critical) - Number(a.critical) || a.hops - b.hops);
    lines.push(`SHORTEST REASONING PATHS (${paths.length}):`);
    for (const p of paths) {
      const a = nodeMap.get(p.fromId);
      const b = nodeMap.get(p.toId);
      if (!a || !b) continue;
      const segments: string[] = [];
      for (let k = 0; k < p.edges.length; k++) {
        const e = p.edges[k];
        const left = p.nodes[k];
        const right = p.nodes[k + 1];
        segments.push(renderEdge(left, e, right));
        for (const pmid of e.pmids ?? []) pmids.add(pmid);
        unionNodes.set(left.id, left); unionNodes.set(right.id, right);
        unionEdges.set(e.id, e);
      }
      lines.push(`  ${a.label} → ${b.label} (${p.hops} hop${p.hops > 1 ? "s" : ""}${p.critical ? ", CRITICAL" : ""}):`);
      for (const s of segments) lines.push(`    ${s}`);
    }
    lines.push("");
  } else if (entities.length > 1) {
    lines.push(`No path of ≤${maxHops} hops found between the mentioned entities.`);
    lines.push("");
  }

  // User-state hints
  if (opts.focusNodeId) {
    const f = nodeMap.get(opts.focusNodeId);
    if (f) lines.push(`USER CONTEXT: currently selected = ${f.label} (${f.type}).`);
  }
  if (visibleSet.size) {
    lines.push(`USER CONTEXT: ${visibleSet.size} nodes currently visible on the canvas.`);
  }

  return {
    question, entities, neighborhoods, paths,
    contextText: lines.join("\n"),
    pmids: [...pmids],
    subgraph: { nodes: [...unionNodes.values()], edges: [...unionEdges.values()] },
  };
}

export function visibleGraphContext(graph: Graph, focusId?: string | null): ContextPacket {
  const lines: string[] = [];
  const pmids = new Set<string>();
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  if (focusId && byId.has(focusId)) {
    lines.push(`User has selected: ${byId.get(focusId)!.label} (${byId.get(focusId)!.type})`);
    lines.push("");
  }
  const nodesByType: Record<string, GraphNode[]> = {};
  for (const n of graph.nodes) (nodesByType[n.type] ||= []).push(n);
  lines.push("Currently visible entities:");
  for (const [type, ns] of Object.entries(nodesByType)) {
    const sample = ns.slice(0, 12).map((n) => n.label).join(", ");
    lines.push(`  - ${type}: ${ns.length} (${sample}${ns.length > 12 ? ", …" : ""})`);
  }
  lines.push("");
  lines.push("Key relationships (truncated to 60):");
  for (const e of graph.edges.slice(0, 60)) {
    const a = byId.get(e.source);
    const b = byId.get(e.target);
    if (!a || !b) continue;
    const verb = EDGE_VERB[e.type] ?? e.type;
    const tag: string[] = [];
    if (e.level) tag.push(`L${e.level}`);
    if (e.critical) tag.push("CRITICAL");
    lines.push(`  - ${a.label} ${verb} ${b.label}${tag.length ? ` [${tag.join(",")}]` : ""}`);
    for (const p of e.pmids ?? []) pmids.add(p);
  }
  return { text: lines.join("\n"), pmids: [...pmids], subgraph: graph };
}
