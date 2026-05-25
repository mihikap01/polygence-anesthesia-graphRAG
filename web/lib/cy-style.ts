// Cytoscape stylesheet, kept in its own module so the canvas component stays
// focused on event wiring.  Colours mirror the Tailwind palette in
// tailwind.config.ts. Tuned for a warm off-white canvas background.

import type { Stylesheet } from "cytoscape";

// Off-white that matches the body background (hsl(40 30% 98%) ≈ #fbf8f3),
// used to halo node labels so they stay readable when they overlap edges.
const BG = "#fbf8f3";
const TEXT = "#283543";          // soft charcoal (matches --foreground)
const EDGE = "rgba(40, 53, 67, 0.30)";
const EDGE_HOVER = "rgba(40, 53, 67, 0.50)";
const EDGE_MUTED = "rgba(40, 53, 67, 0.18)";
const RISK = "#dc2626";
const RING = "#4b8d9d";          // primary teal-blue

const NODE_BASE: Record<string, any> = {
  label: "data(label)",
  color: TEXT,
  "font-size": 11,
  "font-weight": 600,
  "text-valign": "bottom",
  "text-margin-y": 6,
  "text-outline-color": BG,
  "text-outline-width": 2,
  "text-wrap": "wrap",
  "text-max-width": "120px",
  "border-width": 1.5,
  "border-color": "rgba(40, 53, 67, 0.25)",
  width: 38, height: 38,
  "transition-property": "background-color, border-color, width, height",
  "transition-duration": 200 as any,
};

export const cytoscapeStyle: Stylesheet[] = [
  { selector: "node", style: NODE_BASE },
  { selector: 'node[type = "drug"]', style: { "background-color": "#2563eb", shape: "round-rectangle" } },
  { selector: 'node[type = "gene"]', style: { "background-color": "#db2777", shape: "ellipse" } },
  { selector: 'node[type = "variant_cluster"]', style: { "background-color": "#ea580c", shape: "round-diamond", width: 32, height: 32 } },
  { selector: 'node[type = "drug_class"]', style: { "background-color": "#7c3aed", shape: "round-hexagon", width: 46, height: 46 } },
  { selector: 'node[type = "phenotype"]', style: { "background-color": "#ca8a04", shape: "round-tag", width: 50, height: 42 } },

  { selector: "node:selected", style: {
    "border-color": RING,
    "border-width": 3,
    "overlay-color": RING,
    "overlay-opacity": 0.08,
  } },

  // edges
  {
    selector: "edge",
    style: {
      width: 1.5,
      "curve-style": "bezier",
      "line-color": EDGE,
      "target-arrow-color": EDGE_HOVER,
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.9,
      "font-size": 9,
      color: TEXT,
      "text-outline-color": BG,
      "text-outline-width": 2,
      "transition-property": "line-color, width",
      "transition-duration": 200 as any,
    },
  },
  // critical / risk edges — red
  {
    selector: 'edge[type = "linked_to_risk"], edge[type = "can_trigger"], edge[?critical]',
    style: {
      "line-color": RISK,
      "target-arrow-color": RISK,
      width: 2.4,
    },
  },
  // structural / class edges — muted
  {
    selector: 'edge[type = "belongs_to_class"], edge[type = "has_variant"]',
    style: {
      "line-color": EDGE_MUTED,
      "target-arrow-color": EDGE_MUTED,
      "line-style": "dashed",
    },
  },
  {
    selector: 'edge[type = "affects_response_to"]',
    style: { "line-color": RING, "target-arrow-color": RING },
  },
  {
    selector: "edge:selected",
    style: { width: 3.5, "line-color": RING, "target-arrow-color": RING },
  },
  {
    selector: ".faded",
    style: { opacity: 0.15 },
  },
  // Filter-driven visibility: applied/removed by GraphCanvas in response to
  // changes in the left-sidebar filter state.  Using a class (not remove +
  // re-add) preserves node positions so toggling a filter doesn't trigger
  // a jarring re-layout.
  {
    selector: ".hidden",
    style: { display: "none" } as any,
  },
];
