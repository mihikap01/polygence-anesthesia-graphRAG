// Client-side UI store: holds the currently-rendered graph, the selection,
// filter state, and the right-sidebar tab.

"use client";

import { create } from "zustand";
import type { Graph, GraphEdge, GraphNode, NodeType, EdgeType } from "./graph/types";

export type RightTab = "explain" | "chat";

export interface UIFilters {
  nodeTypes: Record<NodeType, boolean>;
  edgeTypes: Record<EdgeType, boolean>;
  evidenceLevels: Record<string, boolean>;
  maxNodes: number;
  collapseVariants: boolean;
}

const defaultFilters: UIFilters = {
  nodeTypes: {
    drug: true, gene: true, variant_cluster: true,
    drug_class: true, phenotype: true,
  },
  edgeTypes: {
    linked_to_risk: true, affects_response_to: true, can_trigger: true,
    has_variant: true, belongs_to_class: true, associated_with: true,
  },
  evidenceLevels: { "1A": true, "1B": true, "2A": true, "2B": true, "3": true, "4": true },
  maxNodes: 80,
  collapseVariants: true,
};

interface State {
  graph: Graph;
  setGraph: (g: Graph) => void;
  selectedNode: GraphNode | null;
  selectedEdge: GraphEdge | null;
  selectNode: (n: GraphNode | null) => void;
  selectEdge: (e: GraphEdge | null) => void;
  rightTab: RightTab;
  setRightTab: (t: RightTab) => void;
  filters: UIFilters;
  setFilters: (f: Partial<UIFilters>) => void;
  toggleNodeType: (t: NodeType) => void;
  toggleEdgeType: (t: EdgeType) => void;
  toggleEvidenceLevel: (lv: string) => void;
  resetFilters: () => void;
  loading: boolean;
  setLoading: (b: boolean) => void;
}

export const useStore = create<State>((set, get) => ({
  graph: { nodes: [], edges: [] },
  setGraph: (g) => set({ graph: g }),
  selectedNode: null,
  selectedEdge: null,
  selectNode: (n) => set({ selectedNode: n, selectedEdge: null, rightTab: "explain" }),
  selectEdge: (e) => set({ selectedEdge: e, selectedNode: null, rightTab: "explain" }),
  rightTab: "explain",
  setRightTab: (t) => set({ rightTab: t }),
  filters: defaultFilters,
  setFilters: (f) => set({ filters: { ...get().filters, ...f } }),
  toggleNodeType: (t) =>
    set((s) => ({
      filters: { ...s.filters, nodeTypes: { ...s.filters.nodeTypes, [t]: !s.filters.nodeTypes[t] } },
    })),
  toggleEdgeType: (t) =>
    set((s) => ({
      filters: { ...s.filters, edgeTypes: { ...s.filters.edgeTypes, [t]: !s.filters.edgeTypes[t] } },
    })),
  toggleEvidenceLevel: (lv) =>
    set((s) => ({
      filters: {
        ...s.filters,
        evidenceLevels: { ...s.filters.evidenceLevels, [lv]: !s.filters.evidenceLevels[lv] },
      },
    })),
  resetFilters: () => set({ filters: defaultFilters }),
  loading: false,
  setLoading: (b) => set({ loading: b }),
}));
