// /api/explain — graph-grounded explanation for a node or edge.
//
// LLM provider chosen by env LLM_PROVIDER (claude-cli | openai | none).
// "none" falls back to a deterministic context dump so the retrieval layer
// is verifiable even without credentials.

import { NextResponse } from "next/server";
import { getFullGraph } from "@/lib/graph/loader";
import { nodeContext, edgeContext } from "@/lib/graph/retrieve";
import { ask } from "@/lib/llm";
import { EXPLAIN_SYSTEM, buildExplainUserPrompt } from "@/lib/llm/prompts";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const { kind, id } = body as { kind: "node" | "edge"; id: string };
  if (!kind || !id) {
    return NextResponse.json({ error: "kind and id required" }, { status: 400 });
  }
  const g = getFullGraph();

  let ctx;
  if (kind === "node") {
    ctx = nodeContext(g, id, 2);
    if (!ctx.focus) return NextResponse.json({ error: "node not found" }, { status: 404 });
  } else {
    const edge = g.edges.find((e) => e.id === id);
    if (!edge) return NextResponse.json({ error: "edge not found" }, { status: 404 });
    ctx = edgeContext(g, edge);
  }

  const userPrompt = buildExplainUserPrompt(ctx.text, kind);
  // What we expose to the UI so it can show the user *exactly* what was sent.
  const contextSent = {
    systemPrompt: EXPLAIN_SYSTEM,
    userPrompt,
    contextText: ctx.text,
    subgraphSize: { nodes: ctx.subgraph.nodes.length, edges: ctx.subgraph.edges.length },
    chars: userPrompt.length + EXPLAIN_SYSTEM.length,
  };

  try {
    const out = await ask({
      systemPrompt: EXPLAIN_SYSTEM,
      userPrompt,
    });
    if (!out) {
      return NextResponse.json({
        explanation: templateExplanation(ctx.text, kind),
        evidence: { pmids: ctx.pmids },
        contextSent,
        ai: { provider: "none" },
      });
    }
    return NextResponse.json({
      explanation: out.text,
      evidence: { pmids: ctx.pmids },
      contextSent,
      ai: { provider: out.provider, cost_usd: out.cost_usd, duration_ms: out.duration_ms },
    });
  } catch (e: any) {
    return NextResponse.json({
      explanation: templateExplanation(ctx.text, kind),
      evidence: { pmids: ctx.pmids },
      contextSent,
      ai: { provider: "error", error: String(e?.message ?? e) },
    });
  }
}

function templateExplanation(text: string, kind: "node" | "edge"): string {
  return (
    `(LLM disabled — set LLM_PROVIDER=claude-cli or LLM_PROVIDER=openai to enable graph-aware reasoning.)\n\n` +
    `Local context for this ${kind}:\n\n${text}`
  );
}
