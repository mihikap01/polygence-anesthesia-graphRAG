// /api/chat — GraphRAG chat agent.
//
// Pipeline:
//   1. Entity-link the question against the graph (fuzzy n-gram match).
//   2. Pull 1-hop neighbourhoods for each linked entity + shortest paths
//      between every pair (this is the *retrieval* in GraphRAG).
//   3. Render a structured context packet for the LLM.
//   4. LLM answers grounded in the retrieved subgraph; we return both the
//      answer AND the retrieval (entities, paths) so the UI can show what
//      was actually consulted.
//
// Provider chosen by env LLM_PROVIDER (claude-cli | openai | none).

import { NextResponse } from "next/server";
import { getFullGraph, getSearchIndex } from "@/lib/graph/loader";
import { retrieveForQuestion } from "@/lib/graph/retrieve";
import { ask } from "@/lib/llm";
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
  getSearchIndex(); // ensure runtime cache is primed before retrieval runs Fuse
  const retrieval = retrieveForQuestion(full, question, { focusNodeId, visibleNodeIds });

  // Slim payload returned to the UI — keep nodes/edges compact for transit.
  const retrievalForClient = {
    entities: retrieval.entities,
    paths: retrieval.paths.map((p) => ({
      fromId: p.fromId,
      toId: p.toId,
      hops: p.hops,
      critical: p.critical,
      nodes: p.nodes.map((n) => ({ id: n.id, label: n.label, type: n.type })),
      edges: p.edges.map((e) => ({
        id: e.id, source: e.source, target: e.target, type: e.type,
        level: e.level, critical: e.critical,
      })),
    })),
    subgraphSize: {
      nodes: retrieval.subgraph.nodes.length,
      edges: retrieval.subgraph.edges.length,
    },
  };

  const userPrompt = buildChatUserPrompt(retrieval.contextText, question);
  const contextSent = {
    systemPrompt: CHAT_SYSTEM,
    userPrompt,
    contextText: retrieval.contextText,
    subgraphSize: retrievalForClient.subgraphSize,
    chars: userPrompt.length + CHAT_SYSTEM.length,
  };

  try {
    const out = await ask({ systemPrompt: CHAT_SYSTEM, userPrompt });
    if (!out) {
      return NextResponse.json({
        answer:
          "(LLM disabled — set LLM_PROVIDER=claude-cli or LLM_PROVIDER=openai to enable chat.)\n\n" +
          "Retrieved graph context:\n\n" +
          retrieval.contextText.slice(0, 2000),
        retrieval: retrievalForClient,
        contextSent,
        citations: { pmids: retrieval.pmids.slice(0, 12) },
        ai: { provider: "none" },
      });
    }
    return NextResponse.json({
      answer: out.text,
      retrieval: retrievalForClient,
      contextSent,
      citations: { pmids: retrieval.pmids.slice(0, 12) },
      ai: { provider: out.provider, cost_usd: out.cost_usd, duration_ms: out.duration_ms },
    });
  } catch (e: any) {
    return NextResponse.json({
      answer: `Error from model: ${String(e?.message ?? e)}`,
      retrieval: retrievalForClient,
      contextSent,
      citations: { pmids: retrieval.pmids.slice(0, 12) },
      ai: { provider: "error", error: String(e?.message ?? e) },
    }, { status: 500 });
  }
}
