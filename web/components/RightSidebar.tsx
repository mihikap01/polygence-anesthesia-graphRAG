"use client";

import { useEffect, useState } from "react";
import {
  MessageSquare, Sparkles, Info, ExternalLink, Send,
  ChevronDown, ChevronRight, Copy, Check, FileText, MousePointerClick,
  KeyRound,
} from "lucide-react";
import { useStore } from "@/lib/store";
import { cn } from "@/lib/cn";
import { isBYOK, getStoredKey } from "@/lib/api-key";
import {
  explainInBrowser, chatInBrowser, MissingApiKeyError,
} from "@/lib/llm/browser-pipeline";
import ApiKeyModal from "@/components/ApiKeyModal";

// ---------- Shared: Context viewer ------------------------------------------

interface ContextSent {
  systemPrompt: string;
  userPrompt: string;
  contextText: string;
  subgraphSize?: { nodes: number; edges: number };
  chars: number;
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
// Rough token estimate: ~3.7 chars/token for English.
function estimateTokens(chars: number) {
  return Math.round(chars / 3.7);
}

function ContextViewer({ context }: { context?: ContextSent }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"context" | "system" | "user">("context");
  const [copied, setCopied] = useState(false);
  if (!context) return null;

  const active =
    tab === "system" ? context.systemPrompt :
    tab === "user" ? context.userPrompt :
    context.contextText;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(active);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  };

  return (
    <div className="mt-3 rounded-md border border-slate-700/50 bg-slate-900/40 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-2.5 py-1.5 flex items-center justify-between text-[10px] text-slate-400 hover:bg-slate-800/40"
      >
        <span className="flex items-center gap-1.5">
          {open ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}
          <FileText size={11}/>
          <span className="font-semibold uppercase tracking-wider">Context sent to model</span>
        </span>
        <span className="flex items-center gap-2 font-mono tabular-nums">
          <span>{formatBytes(context.chars)}</span>
          <span className="opacity-50">·</span>
          <span>~{estimateTokens(context.chars).toLocaleString()} tok</span>
          {context.subgraphSize && (
            <>
              <span className="opacity-50">·</span>
              <span>{context.subgraphSize.nodes}n·{context.subgraphSize.edges}e</span>
            </>
          )}
        </span>
      </button>
      {open && (
        <div className="border-t border-slate-700/50">
          <div className="flex border-b border-slate-800/60 text-[10px]">
            {(["context", "system", "user"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "px-2.5 py-1.5 font-medium uppercase tracking-wider transition-colors",
                  tab === t ? "text-blue-300 bg-slate-800/60" : "text-slate-500 hover:text-slate-300"
                )}
              >
                {t === "context" ? "Graph context" : t === "system" ? "System prompt" : "User prompt"}
              </button>
            ))}
            <div className="ml-auto flex items-center pr-1">
              <button onClick={copy}
                      className="px-2 py-1 text-[10px] text-slate-400 hover:text-white flex items-center gap-1">
                {copied ? <><Check size={11}/> copied</> : <><Copy size={11}/> copy</>}
              </button>
            </div>
          </div>
          <pre className="text-[10.5px] leading-relaxed text-slate-300 p-2.5 max-h-72 overflow-auto font-mono whitespace-pre-wrap break-words">
{active}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function RightSidebar() {
  const tab = useStore((s) => s.rightTab);
  const setTab = useStore((s) => s.setRightTab);
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyVersion, setKeyVersion] = useState(0); // bumps after Save to invalidate caches
  const byok = isBYOK();
  return (
    <aside className="w-[400px] shrink-0 h-full flex flex-col card border-l border-y-0 border-r-0">
      <div className="flex border-b border-slate-800/60">
        <TabBtn active={tab === "explain"} onClick={() => setTab("explain")}>
          <Sparkles size={13}/> Explain
        </TabBtn>
        <TabBtn active={tab === "chat"} onClick={() => setTab("chat")}>
          <MessageSquare size={13}/> Chat
        </TabBtn>
        {byok && (
          <button
            onClick={() => setKeyModalOpen(true)}
            className="px-3 text-slate-400 hover:text-blue-300 border-l border-slate-800/60"
            title="Manage your API key"
          >
            <KeyRound size={13}/>
          </button>
        )}
      </div>
      <div className="px-3 py-2 border-b border-slate-800/60 bg-slate-900/30">
        {tab === "explain" ? (
          <div className="flex items-start gap-2 text-[10.5px] text-slate-400 leading-snug">
            <MousePointerClick size={12} className="mt-0.5 shrink-0 text-blue-400/80"/>
            <span>
              <span className="text-slate-200 font-medium">Click-driven.</span>{" "}
              Select any node or edge on the canvas — Claude explains it using its 2-hop graph neighbourhood.
            </span>
          </div>
        ) : (
          <div className="flex items-start gap-2 text-[10.5px] text-slate-400 leading-snug">
            <MessageSquare size={12} className="mt-0.5 shrink-0 text-blue-400/80"/>
            <span>
              <span className="text-slate-200 font-medium">Question-driven.</span>{" "}
              Type a question — GraphRAG entity-links it to the graph, retrieves paths, then Claude answers.
            </span>
          </div>
        )}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === "explain"
          ? <ExplainPanel byok={byok} openKeyModal={() => setKeyModalOpen(true)} keyVersion={keyVersion}/>
          : <ChatPanel byok={byok} openKeyModal={() => setKeyModalOpen(true)} keyVersion={keyVersion}/>}
      </div>
      <ApiKeyModal
        open={keyModalOpen}
        onClose={() => setKeyModalOpen(false)}
        onSaved={() => setKeyVersion((v) => v + 1)}
      />
    </aside>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex-1 px-3 py-2.5 text-xs font-medium flex items-center justify-center gap-1.5 transition-colors",
        active ? "text-white bg-slate-800/70 border-b-2 border-blue-500" : "text-slate-400 hover:text-slate-200"
      )}
    >
      {children}
    </button>
  );
}

// ---------- Explain ---------------------------------------------------------

interface PanelProps {
  byok: boolean;
  openKeyModal: () => void;
  keyVersion: number;
}

function ExplainPanel({ byok, openKeyModal, keyVersion }: PanelProps) {
  const node = useStore((s) => s.selectedNode);
  const edge = useStore((s) => s.selectedEdge);
  const [explanation, setExplanation] = useState<string>("");
  const [evidence, setEvidence] = useState<any>(null);
  const [contextSent, setContextSent] = useState<ContextSent | undefined>(undefined);
  const [ai, setAi] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);

  useEffect(() => {
    if (!node && !edge) {
      setExplanation(""); setEvidence(null); setContextSent(undefined);
      setAi(null); setError(null); setNeedsKey(false); return;
    }
    setBusy(true);
    setError(null);
    setNeedsKey(false);
    const args = node
      ? { kind: "node" as const, id: node.id }
      : { kind: "edge" as const, id: edge!.id };

    const work = byok
      ? explainInBrowser(args)
      : fetch("/api/explain", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(args),
        }).then(async (r) => {
          const j = await r.json();
          if (!r.ok) throw new Error(j.error || "Failed");
          return j;
        });

    work
      .then((j: any) => {
        setExplanation(j.explanation || "");
        setEvidence(j.evidence || null);
        setContextSent(j.contextSent);
        setAi(j.ai);
      })
      .catch((e) => {
        if (e instanceof MissingApiKeyError) {
          setNeedsKey(true);
          openKeyModal();
        } else {
          setError(String(e.message ?? e));
        }
      })
      .finally(() => setBusy(false));
  }, [node?.id, edge?.id, byok, keyVersion]);

  if (!node && !edge) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400">
        <MousePointerClick size={28} className="text-slate-600 mb-3"/>
        <div className="text-xs font-medium text-slate-300 mb-1">Nothing selected yet</div>
        <div className="text-[11px] text-slate-500 leading-snug max-w-[260px]">
          Click any <span className="text-slate-300">node</span> or <span className="text-red-400">edge</span> on the
          graph to the left — Claude will explain it using its 2-hop graph neighbourhood.
        </div>
        <div className="text-[10px] text-slate-600 mt-3">
          For freeform questions, switch to the <span className="text-slate-400">Chat</span> tab.
        </div>
      </div>
    );
  }

  const title = node ? node.label : `${edge!.source} → ${edge!.target}`;
  const subtitle = node ? labelForNodeType(node.type) : edge!.type.replace(/_/g, " ");

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-3 border-b border-slate-800/60">
        <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">{subtitle}</div>
        <div className="text-sm font-semibold text-white mt-0.5 truncate" title={title}>{title}</div>
        {node?.fullName && node.fullName !== node.label && (
          <div className="text-[11px] text-slate-400 mt-0.5">{node.fullName}</div>
        )}
        {(node?.pharmgkb_id || edge?.level) && (
          <div className="flex flex-wrap gap-1 mt-2">
            {node?.pharmgkb_id && (
              <a href={`https://www.pharmgkb.org/${node.type === "drug" ? "chemical" : node.type === "gene" ? "gene" : "disease"}/${node.pharmgkb_id}`}
                 target="_blank" rel="noreferrer" className="chip hover:bg-slate-700/80">
                PharmGKB <ExternalLink size={9} />
              </a>
            )}
            {edge?.level && <span className="chip">Level {edge.level}</span>}
            {edge?.critical && <span className="chip" style={{ background: "rgba(239,68,68,0.2)", borderColor: "rgba(239,68,68,0.4)" }}>critical</span>}
            {node?.is_vip && <span className="chip">VIP gene</span>}
            {node?.members && <span className="chip">{node.members.length} variants</span>}
          </div>
        )}
      </div>

      <div className="p-3 overflow-y-auto flex-1 text-xs leading-relaxed">
        {busy && (
          <div className="flex items-center gap-2 text-slate-400">
            <span className="dots"><span/><span/><span/></span> Reasoning over graph context…
          </div>
        )}
        {error && <div className="text-red-400">{error}</div>}
        {needsKey && !busy && (
          <button
            onClick={openKeyModal}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md bg-blue-600/20 border border-blue-500/40 text-blue-200 hover:bg-blue-600/30"
          >
            <KeyRound size={12}/> Add your LLM API key to Explain
          </button>
        )}
        {!busy && !error && !needsKey && (
          <>
            {ai?.provider && ai.provider !== "none" && (
              <div className="text-[10px] text-slate-500 mb-2 font-mono">
                {ai.provider}
                {ai.duration_ms ? ` · ${(ai.duration_ms / 1000).toFixed(1)}s` : ""}
                {ai.cost_usd ? ` · $${ai.cost_usd.toFixed(3)}` : ""}
              </div>
            )}
            <p className="whitespace-pre-wrap text-slate-200">{explanation}</p>
            {evidence?.pmids?.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">PMIDs</div>
                <div className="flex flex-wrap gap-1">
                  {evidence.pmids.slice(0, 12).map((pmid: string) => (
                    <a key={pmid} href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                       target="_blank" rel="noreferrer" className="chip hover:bg-slate-700/80">{pmid}</a>
                  ))}
                </div>
              </div>
            )}
            {node?.members && node.members.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
                  Variants ({node.members.length})
                </div>
                <div className="flex flex-wrap gap-1 max-h-40 overflow-y-auto">
                  {node.members.slice(0, 60).map((m, i) => (
                    <a key={i}
                       href={m.rsid.startsWith("rs") ? `https://www.ncbi.nlm.nih.gov/snp/${m.rsid}` : "#"}
                       target="_blank" rel="noreferrer"
                       className="chip font-mono text-[10px] hover:bg-slate-700/80">
                      {m.rsid}{m.level ? ` · ${m.level}` : ""}
                    </a>
                  ))}
                </div>
              </div>
            )}
            <ContextViewer context={contextSent} />
          </>
        )}
      </div>
    </div>
  );
}

function labelForNodeType(t: string) {
  return ({
    drug: "DRUG", gene: "GENE", variant_cluster: "VARIANT CLUSTER",
    drug_class: "DRUG CLASS", phenotype: "PHENOTYPE",
  } as Record<string, string>)[t] || t.toUpperCase();
}

// ---------- Chat ------------------------------------------------------------

interface RetrievalEntity { id: string; label: string; type: string; matchedTerm: string; score: number }
interface RetrievalPath {
  fromId: string; toId: string; hops: number; critical: boolean;
  nodes: Array<{ id: string; label: string; type: string }>;
  edges: Array<{ id: string; type: string; level?: string; critical?: boolean }>;
}
interface ChatRetrieval {
  entities: RetrievalEntity[];
  paths: RetrievalPath[];
  subgraphSize: { nodes: number; edges: number };
}
interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  citations?: any;
  retrieval?: ChatRetrieval;
  contextSent?: ContextSent;
  ai?: { provider?: string; duration_ms?: number; cost_usd?: number };
}

const ENTITY_COLOR: Record<string, string> = {
  drug: "#3b82f6", gene: "#ec4899", variant_cluster: "#f97316",
  drug_class: "#a855f7", phenotype: "#eab308",
};

function RetrievalStrip({ r, ai }: { r: ChatRetrieval; ai?: ChatMsg["ai"] }) {
  if (!r) return null;
  const hasContent = r.entities.length > 0 || r.paths.length > 0;
  if (!hasContent) {
    return (
      <div className="text-[10px] text-slate-500 mb-2 italic">
        no graph entities matched — answering from visible context only
      </div>
    );
  }
  return (
    <div className="mb-2 pb-2 border-b border-slate-700/40">
      <div className="text-[9px] uppercase tracking-wider text-slate-500 font-semibold mb-1 flex items-center gap-2">
        <span>GraphRAG retrieved</span>
        {ai?.provider && ai.provider !== "none" && ai.provider !== "error" && (
          <span className="text-slate-600 normal-case tracking-normal font-normal">
            · {ai.provider}
            {ai.duration_ms ? ` · ${(ai.duration_ms / 1000).toFixed(1)}s` : ""}
            {ai.cost_usd ? ` · $${ai.cost_usd.toFixed(3)}` : ""}
          </span>
        )}
      </div>
      {r.entities.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1.5">
          {r.entities.map((e) => (
            <span key={e.id} className="chip text-[10px]"
                  title={`matched "${e.matchedTerm}" · score ${e.score.toFixed(2)}`}>
              <span style={{ background: ENTITY_COLOR[e.type] || "#64748b" }}
                    className="w-1.5 h-1.5 rounded-full inline-block" />
              {e.label}
            </span>
          ))}
        </div>
      )}
      {r.paths.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[9px] uppercase tracking-wider text-slate-500 font-semibold mt-1.5">
            paths ({r.paths.length})
          </div>
          {r.paths.slice(0, 4).map((p, i) => (
            <div key={i} className={cn(
              "text-[10px] leading-tight flex items-center gap-1 font-mono",
              p.critical ? "text-red-300" : "text-slate-400"
            )}>
              <span className="opacity-60">[{p.hops}h{p.critical ? "·!" : ""}]</span>
              <span className="truncate">
                {p.nodes.map((n, j) => (
                  <span key={j}>
                    {j > 0 && <span className="mx-0.5 opacity-40">→</span>}
                    {n.label}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="text-[9px] text-slate-600 mt-1.5">
        subgraph: {r.subgraphSize.nodes} nodes · {r.subgraphSize.edges} edges
      </div>
    </div>
  );
}

function ChatPanel({ byok, openKeyModal, keyVersion: _keyVersion }: PanelProps) {
  const node = useStore((s) => s.selectedNode);
  const graph = useStore((s) => s.graph);
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Ask me anything about the current graph. Try: \"Why is sevoflurane risky for malignant hyperthermia?\" or \"How does RYR1 connect to this phenotype?\"",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    // BYOK pre-check: open modal first if no key yet, don't burn a turn.
    if (byok && !getStoredKey()) {
      openKeyModal();
      return;
    }
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const args = {
        question: q,
        focusNodeId: node?.id ?? null,
        visibleNodeIds: graph.nodes.map((n) => n.id),
      };
      const j = byok
        ? await chatInBrowser(args)
        : await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(args),
          }).then((r) => r.json());
      setMessages((m) => [...m, {
        role: "assistant",
        content: (j as any).answer || (j as any).error || "(no answer)",
        citations: (j as any).citations,
        retrieval: (j as any).retrieval,
        contextSent: (j as any).contextSent,
        ai: (j as any).ai,
      }]);
    } catch (e: any) {
      if (e instanceof MissingApiKeyError) {
        openKeyModal();
        setMessages((m) => m.slice(0, -1)); // remove the user msg since we didn't process it
      } else {
        setMessages((m) => [...m, { role: "assistant", content: `Error: ${e?.message ?? e}` }]);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((m, i) => (
          <div key={i} className={cn("text-xs leading-relaxed", m.role === "user" ? "text-right" : "")}>
            <div className={cn(
              "inline-block max-w-[92%] px-3 py-2 rounded-lg text-left",
              m.role === "user"
                ? "bg-blue-600/30 border border-blue-500/30 text-blue-50"
                : "bg-slate-800/70 border border-slate-700/60 text-slate-200"
            )}>
              {m.role === "assistant" && m.retrieval && (
                <RetrievalStrip r={m.retrieval} ai={m.ai} />
              )}
              <div className="whitespace-pre-wrap">{m.content}</div>
              {m.citations?.pmids?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {m.citations.pmids.slice(0, 8).map((p: string) => (
                    <a key={p} href={`https://pubmed.ncbi.nlm.nih.gov/${p}/`}
                       target="_blank" rel="noreferrer"
                       className="chip text-[10px] hover:bg-slate-700/80">{p}</a>
                  ))}
                </div>
              )}
              {m.role === "assistant" && m.contextSent && (
                <ContextViewer context={m.contextSent} />
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="text-xs text-slate-400 flex items-center gap-2">
            <span className="dots"><span/><span/><span/></span> Searching graph…
          </div>
        )}
      </div>
      <div className="p-2 border-t border-slate-800/60 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={node ? `Ask about ${node.label}…` : "Ask about the visible graph…"}
          className="flex-1 px-3 py-2 text-xs rounded-md bg-slate-900/60 border border-slate-700/60 focus:border-blue-500/60 outline-none placeholder:text-slate-500"
        />
        <button className="btn-primary btn" onClick={send} disabled={busy}>
          <Send size={12}/>
        </button>
      </div>
    </div>
  );
}
