"use client";

// Browser-side equivalents of /api/explain and /api/chat. The response shape
// matches the server routes 1:1 so RightSidebar just calls these in the
// NEXT_PUBLIC_BYOK build.
//
// Default path: proxyAsk() → /api/llm (Cloud Function with DeepSeek key).
// Override: if the user pasted their own OpenAI/Anthropic/DeepSeek key in the
// modal, we skip the proxy and call the provider directly from the browser.

import {
  nodeContext,
  edgeContext,
  retrieveForQuestion,
} from "@/lib/graph/retrieve";
import { ensureGraphLoaded } from "@/lib/graph/client-loader";
import { getStoredKey } from "@/lib/api-key";
import { browserAsk, proxyAsk, type BrowserAskOutput } from "@/lib/llm/browser";
import {
  EXPLAIN_SYSTEM,
  CHAT_SYSTEM,
  buildExplainUserPrompt,
  buildChatUserPrompt,
} from "@/lib/llm/prompts";

async function ask(input: {
  systemPrompt: string;
  userPrompt: string;
}): Promise<BrowserAskOutput> {
  const stored = getStoredKey();
  if (stored) return browserAsk(stored, input);
  return proxyAsk(input);
}

export async function explainInBrowser(args: {
  kind: "node" | "edge";
  id: string;
}) {
  const { full } = await ensureGraphLoaded();

  let ctx;
  if (args.kind === "node") {
    ctx = nodeContext(full, args.id, 2);
    if (!ctx.focus) throw new Error("node not found");
  } else {
    const edge = full.edges.find((e) => e.id === args.id);
    if (!edge) throw new Error("edge not found");
    ctx = edgeContext(full, edge);
  }

  const userPrompt = buildExplainUserPrompt(ctx.text, args.kind);
  const contextSent = {
    systemPrompt: EXPLAIN_SYSTEM,
    userPrompt,
    contextText: ctx.text,
    subgraphSize: {
      nodes: ctx.subgraph.nodes.length,
      edges: ctx.subgraph.edges.length,
    },
    chars: userPrompt.length + EXPLAIN_SYSTEM.length,
  };

  const out = await ask({ systemPrompt: EXPLAIN_SYSTEM, userPrompt });
  return {
    explanation: out.text,
    evidence: { pmids: ctx.pmids },
    contextSent,
    ai: {
      provider: out.provider,
      duration_ms: out.duration_ms,
      model: out.model,
    },
  };
}

export async function chatInBrowser(args: {
  question: string;
  focusNodeId?: string | null;
  visibleNodeIds?: string[];
}) {
  const { full } = await ensureGraphLoaded();

  const retrieval = retrieveForQuestion(full, args.question, {
    focusNodeId: args.focusNodeId,
    visibleNodeIds: args.visibleNodeIds,
  });

  const retrievalForClient = {
    entities: retrieval.entities,
    paths: retrieval.paths.map((p) => ({
      fromId: p.fromId,
      toId: p.toId,
      hops: p.hops,
      critical: p.critical,
      nodes: p.nodes.map((n) => ({ id: n.id, label: n.label, type: n.type })),
      edges: p.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: e.type,
        level: e.level,
        critical: e.critical,
      })),
    })),
    subgraphSize: {
      nodes: retrieval.subgraph.nodes.length,
      edges: retrieval.subgraph.edges.length,
    },
  };

  const userPrompt = buildChatUserPrompt(retrieval.contextText, args.question);
  const contextSent = {
    systemPrompt: CHAT_SYSTEM,
    userPrompt,
    contextText: retrieval.contextText,
    subgraphSize: retrievalForClient.subgraphSize,
    chars: userPrompt.length + CHAT_SYSTEM.length,
  };

  const out = await ask({ systemPrompt: CHAT_SYSTEM, userPrompt });
  return {
    answer: out.text,
    retrieval: retrievalForClient,
    contextSent,
    citations: { pmids: retrieval.pmids.slice(0, 12) },
    ai: {
      provider: out.provider,
      duration_ms: out.duration_ms,
      model: out.model,
    },
  };
}
