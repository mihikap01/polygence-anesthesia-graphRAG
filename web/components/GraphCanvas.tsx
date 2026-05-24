"use client";

import { useEffect, useRef } from "react";
import cytoscape, { type Core, type ElementsDefinition } from "cytoscape";
// @ts-expect-error - no types ship with the plugin
import fcose from "cytoscape-fcose";
import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { cytoscapeStyle } from "@/lib/cy-style";
import { useStore } from "@/lib/store";
import type { Graph } from "@/lib/graph/types";

cytoscape.use(fcose as any);

function toElements(g: Graph): ElementsDefinition {
  return {
    nodes: g.nodes.map((n) => ({ data: { ...n } })),
    edges: g.edges.map((e) => ({ data: { ...e } })),
  };
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
  const selectNode = useStore((s) => s.selectNode);
  const selectEdge = useStore((s) => s.selectEdge);

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

  // sync graph data
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().remove();
    cy.add(toElements(graph));
    cy.layout(LAYOUT as any).run();
  }, [graph]);

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
