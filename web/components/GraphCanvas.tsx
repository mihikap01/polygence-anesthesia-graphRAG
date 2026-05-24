"use client";

import { useEffect, useMemo, useRef } from "react";
import cytoscape, { type Core, type ElementsDefinition } from "cytoscape";
// @ts-expect-error - no types ship with the plugin
import fcose from "cytoscape-fcose";
import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { cytoscapeStyle } from "@/lib/cy-style";
import { useStore, type UIFilters } from "@/lib/store";
import type { Graph, GraphEdge, GraphNode } from "@/lib/graph/types";

cytoscape.use(fcose as any);

function toElements(g: Graph): ElementsDefinition {
  return {
    nodes: g.nodes.map((n) => ({ data: { ...n } })),
    edges: g.edges.map((e) => ({ data: { ...e } })),
  };
}

// When collapseVariants is OFF, each variant_cluster is replaced by one
// node per member rsID, all parented to the cluster's gene via has_variant.
// This is the only filter that changes node *count* (and therefore needs a
// re-layout); everything else is applied as a visibility class.
function expandVariantClusters(g: Graph): Graph {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [...g.edges];
  const clusterIdToGene = new Map<string, string>();
  for (const e of g.edges) {
    if (e.type === "has_variant") clusterIdToGene.set(e.target, e.source);
  }
  let nextId = g.edges.length;
  for (const n of g.nodes) {
    if (n.type !== "variant_cluster" || !n.members?.length) {
      nodes.push(n);
      continue;
    }
    const geneId = clusterIdToGene.get(n.id);
    // keep the cluster node but mark it tiny? simpler: drop it entirely
    // and add member nodes connected directly to the parent gene.
    for (const m of n.members) {
      const vid = `${n.id}__${m.rsid}`;
      nodes.push({
        id: vid,
        label: m.rsid,
        type: "variant_cluster",  // reuse style; treated as an individual variant
        gene: n.gene,
        level: m.level,
        members: [m],
      } as GraphNode);
      if (geneId) {
        edges.push({
          id: `eexp${nextId++}`,
          source: geneId,
          target: vid,
          type: "has_variant",
          level: m.level,
        });
      }
    }
    // remove the original cluster + its has_variant edge
  }
  // drop edges that referenced removed clusters
  const liveNodeIds = new Set(nodes.map((n) => n.id));
  const cleanedEdges = edges.filter(
    (e) => liveNodeIds.has(e.source) && liveNodeIds.has(e.target)
  );
  return { nodes, edges: cleanedEdges };
}

// Decide which elements should be hidden, based on the boolean filters.
// Returns ids of nodes / edges to hide.  maxNodes (degree-ranked top-K) is
// applied last so type/evidence filtering happens first.
function computeHidden(g: Graph, f: UIFilters): { nodes: Set<string>; edges: Set<string> } {
  const hideNodes = new Set<string>();
  const hideEdges = new Set<string>();

  for (const n of g.nodes) {
    if (!f.nodeTypes[n.type]) hideNodes.add(n.id);
  }
  for (const e of g.edges) {
    if (!f.edgeTypes[e.type]) hideEdges.add(e.id);
    else if (e.level && !f.evidenceLevels[e.level]) hideEdges.add(e.id);
    else if (hideNodes.has(e.source) || hideNodes.has(e.target)) hideEdges.add(e.id);
  }

  // maxNodes: keep the top-K nodes by *visible* edge degree.
  const visibleNodes = g.nodes.filter((n) => !hideNodes.has(n.id));
  if (f.maxNodes && visibleNodes.length > f.maxNodes) {
    const deg = new Map<string, number>();
    for (const e of g.edges) {
      if (hideEdges.has(e.id)) continue;
      deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
      deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
    }
    const ranked = [...visibleNodes].sort(
      (a, b) => (deg.get(b.id) ?? 0) - (deg.get(a.id) ?? 0)
    );
    for (const n of ranked.slice(f.maxNodes)) hideNodes.add(n.id);
    // re-hide edges whose endpoints just became hidden
    for (const e of g.edges) {
      if (hideNodes.has(e.source) || hideNodes.has(e.target)) hideEdges.add(e.id);
    }
  }

  return { nodes: hideNodes, edges: hideEdges };
}

const LAYOUT = {
  name: "fcose",
  quality: "proof",
  animate: true,
  animationDuration: 600,
  nodeSeparation: 90,
  idealEdgeLength: 110,
  edgeElasticity: 0.45,
  gravity: 0.18,
  gravityRange: 3.0,
  packComponents: true,
  randomize: true,
};

export default function GraphCanvas() {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const graph = useStore((s) => s.graph);
  const filters = useStore((s) => s.filters);
  const selectNode = useStore((s) => s.selectNode);
  const selectEdge = useStore((s) => s.selectEdge);

  // collapseVariants is structural — it changes which nodes exist, so it
  // belongs in the data we hand to cytoscape (and triggers a re-layout).
  const renderedGraph = useMemo<Graph>(
    () => (filters.collapseVariants ? graph : expandVariantClusters(graph)),
    [graph, filters.collapseVariants]
  );

  // initialise cytoscape once
  useEffect(() => {
    if (!ref.current || cyRef.current) return;
    cyRef.current = cytoscape({
      container: ref.current,
      elements: { nodes: [], edges: [] },
      style: cytoscapeStyle,
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 3,
      boxSelectionEnabled: false,
    });
    const cy = cyRef.current;
    cy.on("tap", "node", (evt) => {
      const data = evt.target.data();
      selectNode({
        id: data.id, label: data.label, type: data.type,
        fullName: data.fullName, pharmgkb_id: data.pharmgkb_id,
        chromosome: data.chromosome, is_vip: data.is_vip,
        atc: data.atc, top_level: data.top_level, gene: data.gene,
        level: data.level, members: data.members,
        xref: data.xref, description: data.description,
      });
      highlightNeighbours(cy, data.id);
    });
    cy.on("tap", "edge", (evt) => {
      const d = evt.target.data();
      selectEdge({
        id: d.id, source: d.source, target: d.target, type: d.type,
        level: d.level, role: d.role, critical: d.critical,
        count: d.count, gene: d.gene, pmids: d.pmids,
      });
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("faded");
        selectNode(null);
      }
    });
    return () => { cy.destroy(); cyRef.current = null; };
  }, [selectNode, selectEdge]);

  // sync graph data → full re-add + re-layout.  Runs when graph data
  // changes OR when collapseVariants flips (because that changes node count).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().remove();
    cy.add(toElements(renderedGraph));
    cy.layout(LAYOUT as any).run();
  }, [renderedGraph]);

  // Apply visibility filters as a class so toggling them doesn't re-layout.
  // Runs on the same renderedGraph effect AND whenever the boolean filters
  // change.  cy.batch keeps it to a single repaint.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || renderedGraph.nodes.length === 0) return;
    const { nodes: hideNodes, edges: hideEdges } = computeHidden(renderedGraph, filters);
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        n[hideNodes.has(n.id()) ? "addClass" : "removeClass"]("hidden");
      });
      cy.edges().forEach((e) => {
        e[hideEdges.has(e.id()) ? "addClass" : "removeClass"]("hidden");
      });
    });
  }, [renderedGraph, filters]);

  const fit = () => cyRef.current?.fit(undefined, 40);
  const reset = () => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded");
    cy.layout(LAYOUT as any).run();
    setTimeout(() => cy.fit(undefined, 40), 650);
  };
  const zoomIn = () => cyRef.current?.zoom({ level: cyRef.current.zoom() * 1.25, position: { x: cyRef.current.width()/2, y: cyRef.current.height()/2 } });
  const zoomOut = () => cyRef.current?.zoom({ level: cyRef.current.zoom() / 1.25, position: { x: cyRef.current.width()/2, y: cyRef.current.height()/2 } });

  return (
    <div className="relative h-full w-full">
      <div ref={ref} className="cy-container" />
      <div className="absolute right-4 top-4 flex flex-col gap-2">
        <button className="btn" title="Fit to screen" onClick={fit}><Maximize2 size={14}/></button>
        <button className="btn" title="Reset layout" onClick={reset}><RotateCcw size={14}/></button>
        <button className="btn" title="Zoom in" onClick={zoomIn}><ZoomIn size={14}/></button>
        <button className="btn" title="Zoom out" onClick={zoomOut}><ZoomOut size={14}/></button>
      </div>
      <Legend />
    </div>
  );
}

function highlightNeighbours(cy: Core, nodeId: string) {
  const n = cy.getElementById(nodeId);
  if (!n || n.empty()) return;
  const nbh = n.closedNeighborhood();
  cy.elements().addClass("faded");
  nbh.removeClass("faded");
}

function Legend() {
  const items: Array<[string, string, string]> = [
    ["#3b82f6", "Drug", "rectangle"],
    ["#a855f7", "Drug class", "hexagon"],
    ["#ec4899", "Gene", "circle"],
    ["#f97316", "Variants", "diamond"],
    ["#eab308", "Phenotype", "tag"],
    ["#ef4444", "Critical risk edge", "edge"],
  ];
  return (
    <div className="absolute left-4 bottom-4 card rounded-xl px-3 py-2 text-xs">
      <div className="font-semibold mb-1.5 text-slate-300">Legend</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        {items.map(([color, label]) => (
          <div key={label} className="flex items-center gap-2">
            <span style={{ background: color }} className="w-2.5 h-2.5 rounded-full inline-block" />
            <span className="text-slate-300">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
