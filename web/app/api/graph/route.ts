// /api/graph
//   ?view=seed                    → curated anesthesia/MH demo subgraph
//   ?seed=<nodeId>&hops=<n>       → neighbourhood expansion centred on a node

import { NextResponse } from "next/server";
import {
  getSeedGraph, getFullGraph, neighbourhood, applyFilters,
} from "@/lib/graph/loader";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const view = url.searchParams.get("view");
  const seed = url.searchParams.get("seed");
  const hops = Number(url.searchParams.get("hops") || "1");
  const maxNodes = Number(url.searchParams.get("maxNodes") || "120");

  if (view === "seed" || (!seed && !view)) {
    return NextResponse.json(getSeedGraph());
  }
  if (seed) {
    const full = getFullGraph();
    const sub = neighbourhood(full, [seed], Math.min(Math.max(hops, 1), 3));
    const limited = applyFilters(sub, { maxNodes });
    return NextResponse.json(limited);
  }
  return NextResponse.json({ nodes: [], edges: [] });
}
