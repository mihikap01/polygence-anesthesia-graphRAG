"use client";

import { useEffect, useState } from "react";
import { MessageSquare, Sparkles, Info, ExternalLink, Send } from "lucide-react";
import { useStore } from "@/lib/store";
import { cn } from "@/lib/cn";

export default function RightSidebar() {
  const tab = useStore((s) => s.rightTab);
  const setTab = useStore((s) => s.setRightTab);
  return (
    <aside className="w-[360px] shrink-0 h-full flex flex-col card border-l border-y-0 border-r-0">
      <div className="flex border-b border-slate-800/60">
        <TabBtn active={tab === "explain"} onClick={() => setTab("explain")}>
          <Sparkles size={13}/> Explain
        </TabBtn>
        <TabBtn active={tab === "chat"} onClick={() => setTab("chat")}>
          <MessageSquare size={13}/> Chat
        </TabBtn>
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === "explain" ? <ExplainPanel /> : <ChatPanel />}
      </div>
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

function ExplainPanel() {
  const node = useStore((s) => s.selectedNode);
  const edge = useStore((s) => s.selectedEdge);
  const [explanation, setExplanation] = useState<string>("");
  const [evidence, setEvidence] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!node && !edge) { setExplanation(""); setEvidence(null); setError(null); return; }
    setBusy(true);
    setError(null);
    const body = node
      ? { kind: "node" as const, id: node.id }
      : { kind: "edge" as const, id: edge!.id };
    fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || "Failed");
        setExplanation(j.explanation || "");
        setEvidence(j.evidence || null);
      })
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setBusy(false));
  }, [node?.id, edge?.id]);

  if (!node && !edge) {
    return (
      <div className="p-4 text-xs text-slate-400 flex items-center gap-2">
        <Info size={14} /> Click a node or edge to see a graph-grounded explanation.
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
        {!busy && !error && (
          <>
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

interface ChatMsg { role: "user" | "assistant"; content: string; citations?: any }

function ChatPanel() {
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
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          focusNodeId: node?.id ?? null,
          visibleNodeIds: graph.nodes.map((n) => n.id),
        }),
      });
      const j = await r.json();
      setMessages((m) => [...m, {
        role: "assistant",
        content: j.answer || j.error || "(no answer)",
        citations: j.citations,
      }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${e?.message ?? e}` }]);
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
              "inline-block max-w-[92%] px-3 py-2 rounded-lg",
              m.role === "user"
                ? "bg-blue-600/30 border border-blue-500/30 text-blue-50"
                : "bg-slate-800/70 border border-slate-700/60 text-slate-200"
            )}>
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
