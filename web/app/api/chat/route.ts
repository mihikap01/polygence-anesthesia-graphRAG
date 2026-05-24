// /api/chat — graph-grounded chat agent.
//
// Builds a context packet from (a) the user's currently-visible graph and
// (b) any focused node, then passes the packet to the LLM with a strict
// "only use these facts" system prompt.  No graph context → no answer.

import { NextResponse } from "next/server";
import { getFullGraph } from "@/lib/graph/loader";
import { visibleGraphContext, nodeContext } from "@/lib/graph/retrieve";
import { getOpenAI, MODEL } from "@/lib/llm/openai";
import { CHAT_SYSTEM, buildChatUserPrompt } from "@/lib/llm/prompts";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const { question, focusNodeId, visibleNodeIds } = body as {
    question: string;
    focusNodeId?: string | null;
    visibleNodeIds?: string[];
  };
  if (!question?.trim()) {
    return NextResponse.json({ error: "question required" }, { status: 400 });
  }

  const full = getFullGraph();
  const visibleSet = new Set(visibleNodeIds ?? []);
  const visibleGraph = visibleSet.size
    ? {
        nodes: full.nodes.filter((n) => visibleSet.has(n.id)),
        edges: full.edges.filter((e) => visibleSet.has(e.source) && visibleSet.has(e.target)),
      }
    : full;

  // Compose context: visible graph summary + (optional) focused-node deep dive
  let contextText = visibleGraphContext(visibleGraph, focusNodeId).text;
  let pmids = visibleGraphContext(visibleGraph, focusNodeId).pmids;
  if (focusNodeId) {
    const deep = nodeContext(full, focusNodeId, 1);
    contextText += "\n\nDeep context for selected node:\n" + deep.text;
    pmids = [...new Set([...pmids, ...deep.pmids])];
  }

  const client = getOpenAI();
  if (!client) {
    return NextResponse.json({
      answer:
        "(AI disabled — set OPENAI_API_KEY to enable chat.)\n\n" +
        "Here is the graph context that would be sent to the model:\n\n" +
        contextText.slice(0, 1200),
      citations: { pmids: pmids.slice(0, 12) },
      ai: "disabled",
    });
  }

  try {
    const resp = await client.chat.completions.create({
      model: MODEL,
      temperature: 0.2,
      messages: [
        { role: "system", content: CHAT_SYSTEM },
        { role: "user", content: buildChatUserPrompt(contextText, question) },
      ],
    });
    const answer = resp.choices[0]?.message?.content ?? "";
    return NextResponse.json({
      answer,
      citations: { pmids: pmids.slice(0, 12) },
      ai: "ok",
    });
  } catch (e: any) {
    return NextResponse.json({
      answer: `Error from model: ${String(e?.message ?? e)}`,
      citations: { pmids: pmids.slice(0, 12) },
      ai: "error",
    }, { status: 500 });
  }
}
