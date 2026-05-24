// Cytoscape stylesheet, kept in its own module so the canvas component stays
// focused on event wiring.  Colours mirror the Tailwind palette in
// tailwind.config.ts.

import type { Stylesheet } from "cytoscape";

const NODE_BASE: Record<string, any> = {
  label: "data(label)",
  color: "#e2e8f0",
  "font-size": 11,
  "font-weight": 600,
  "text-valign": "bottom",
  "text-margin-y": 6,
  "text-outline-color": "#0a0e1a",
  "text-outline-width": 2,
  "text-wrap": "wrap",
  "text-max-width": "120px",
  "border-width": 2,
  "border-color": "#0a0e1a",
  width: 38, height: 38,
  "transition-property": "background-color, border-color, width, height",
  "transition-duration": 200 as any,
};

export const cytoscapeStyle: Stylesheet[] = [
  { selector: "node", style: NODE_BASE },
  { selector: 'node[type = "drug"]', style: { "background-color": "#3b82f6", shape: "round-rectangle" } },
  { selector: 'node[type = "gene"]', style: { "background-color": "#ec4899", shape: "ellipse" } },
  { selector: 'node[type = "variant_cluster"]', style: { "background-color": "#f97316", shape: "round-diamond", width: 32, height: 32 } },
  { selector: 'node[type = "drug_class"]', style: { "background-color": "#a855f7", shape: "round-hexagon", width: 46, height: 46 } },
  { selector: 'node[type = "phenotype"]', style: { "background-color": "#eab308", shape: "round-tag", width: 50, height: 42 } },

  { selector: "node:selected", style: {
    "border-color": "#f8fafc",
    "border-width": 3,
    "overlay-color": "#ffffff",
    "overlay-opacity": 0.05,
  } },

  // edges
  {
    selector: "edge",
    style: {
      width: 1.5,
      "curve-style": "bezier",
      "line-color": "rgba(148, 163, 184, 0.35)",
      "target-arrow-color": "rgba(148, 163, 184, 0.45)",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.9,
      "font-size": 9,
      color: "#94a3b8",
      "text-outline-color": "#0a0e1a",
      "text-outline-width": 2,
      "transition-property": "line-color, width",
      "transition-duration": 200 as any,
    },
  },
  // critical / risk edges — red
  {
    selector: 'edge[type = "linked_to_risk"], edge[type = "can_trigger"], edge[?critical]',
    style: {
      "line-color": "#ef4444",
      "target-arrow-color": "#ef4444",
      width: 2.4,
    },
  },
  // structural / class edges — muted
  {
    selector: 'edge[type = "belongs_to_class"], edge[type = "has_variant"]',
    style: {
      "line-color": "rgba(148, 163, 184, 0.18)",
      "target-arrow-color": "rgba(148, 163, 184, 0.25)",
      "line-style": "dashed",
    },
  },
  {
    selector: 'edge[type = "affects_response_to"]',
    style: { "line-color": "#60a5fa", "target-arrow-color": "#60a5fa" },
  },
  {
    selector: "edge:selected",
    style: { width: 3.5, "line-color": "#f8fafc", "target-arrow-color": "#f8fafc" },
  },
  {
    selector: ".faded",
    style: { opacity: 0.12 },
  },
];
