// Retrieval / Context agent.
//
// Builds compact, graph-grounded "context packets" that the explanation and
// chat agents pass to the LLM.  The packet is plain text — much cheaper for
// the model than raw JSON and produces noticeably better answers.

import type { Graph, GraphEdge, GraphNode } from "./types";
import { neighbourhood } from "./loader";

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
