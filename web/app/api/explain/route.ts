// /api/explain — generates a graph-grounded explanation for a node or edge.
// Falls back to a deterministic template if no OPENAI_API_KEY is configured.

import { NextResponse } from "next/server";
import { getFullGraph } from "@/lib/graph/loader";
import { nodeContext, edgeContext } from "@/lib/graph/retrieve";
import { getOpenAI, MODEL } from "@/lib/llm/openai";
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

  const client = getOpenAI();
  if (!client) {
    return NextResponse.json({
      explanation: templateExplanation(ctx.text, kind),
      evidence: { pmids: ctx.pmids },
      ai: "disabled",
    });
  }

  try {
    const resp = await client.chat.completions.create({
      model: MODEL,
      temperature: 0.2,
      messages: [
        { role: "system", content: EXPLAIN_SYSTEM },
        { role: "user", content: buildExplainUserPrompt(ctx.text, kind) },
      ],
    });
    const explanation = resp.choices[0]?.message?.content ?? "";
    return NextResponse.json({
      explanation,
      evidence: { pmids: ctx.pmids },
      ai: "ok",
    });
  } catch (e: any) {
    return NextResponse.json({
      explanation: templateExplanation(ctx.text, kind),
      evidence: { pmids: ctx.pmids },
      ai: "error",
      error: String(e?.message ?? e),
    });
  }
}

function templateExplanation(text: string, kind: "node" | "edge"): string {
  return (
    `(AI disabled — set OPENAI_API_KEY to enable graph-aware reasoning.)\n\n` +
    `Local context for this ${kind}:\n\n${text}`
  );
}
