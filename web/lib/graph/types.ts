// Shared graph types used by the API, the loader, and the React store.

export type NodeType =
  | "drug"
  | "gene"
  | "variant_cluster"
  | "drug_class"
  | "phenotype";

export type EdgeType =
  | "linked_to_risk"
  | "affects_response_to"
  | "can_trigger"
  | "has_variant"
  | "belongs_to_class"
  | "associated_with";

export interface GraphNode {
  id: string;
  label: string;
  type: NodeType;
  // optional metadata — depends on type
  fullName?: string;
  pharmgkb_id?: string;
  chromosome?: string;
  is_vip?: boolean;
  atc?: string;
  top_level?: string;
  gene?: string;
  level?: string;
  members?: Array<{ rsid: string; level?: string; role?: string; chemical?: string }>;
  xref?: string;
  description?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: EdgeType;
  level?: string;
  role?: string;
  critical?: boolean;
  count?: number;
  gene?: string;
  pmids?: string[];
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface SearchHit {
  id: string;
  label: string;
  type: NodeType;
  alt?: string;
}
