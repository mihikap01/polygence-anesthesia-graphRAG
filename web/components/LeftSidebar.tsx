"use client";

import { useEffect, useMemo, useState } from "react";
import Fuse from "fuse.js";
import { Search, RefreshCw, Filter as FilterIcon } from "lucide-react";
import { useStore } from "@/lib/store";
import type { SearchHit, NodeType, EdgeType } from "@/lib/graph/types";

const NODE_LABELS: Record<NodeType, string> = {
  drug: "Drugs",
  drug_class: "Drug classes",
  gene: "Genes",
  variant_cluster: "Variants",
  phenotype: "Phenotypes",
};
const EDGE_LABELS: Record<EdgeType, string> = {
  linked_to_risk: "linked to risk",
  can_trigger: "can trigger",
  affects_response_to: "affects response to",
  associated_with: "associated with",
  has_variant: "has variant",
  belongs_to_class: "belongs to class",
};
const EVIDENCE_LEVELS = ["1A", "1B", "2A", "2B", "3", "4"];

export default function LeftSidebar() {
  const filters = useStore((s) => s.filters);
  const toggleNodeType = useStore((s) => s.toggleNodeType);
  const toggleEdgeType = useStore((s) => s.toggleEdgeType);
  const toggleEvidenceLevel = useStore((s) => s.toggleEvidenceLevel);
  const setFilters = useStore((s) => s.setFilters);
  const resetFilters = useStore((s) => s.resetFilters);
  const setGraph = useStore((s) => s.setGraph);
  const setLoading = useStore((s) => s.setLoading);

  const [hits, setHits] = useState<SearchHit[]>([]);
  const [q, setQ] = useState("");
  const [allHits, setAllHits] = useState<SearchHit[]>([]);

  useEffect(() => {
    fetch("/api/search").then((r) => r.json()).then(setAllHits);
  }, []);

  const fuse = useMemo(
    () => new Fuse(allHits, { keys: ["label", "alt"], threshold: 0.35, includeScore: true }),
    [allHits]
  );

  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    setHits(fuse.search(q).slice(0, 12).map((r) => r.item));
  }, [q, fuse]);

  async function loadFor(id: string) {
    setLoading(true);
    const r = await fetch(`/api/graph?seed=${encodeURIComponent(id)}&hops=2`);
    const g = await r.json();
    setGraph(g);
    setLoading(false);
  }

  async function loadSeed() {
    setLoading(true);
    const r = await fetch("/api/graph?view=seed");
    setGraph(await r.json());
    setLoading(false);
  }

  return (
    <aside className="w-72 shrink-0 h-full flex flex-col card border-r border-y-0 border-l-0">
      <div className="p-3 border-b border-slate-800/60">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full bg-blue-500" />
          <h1 className="text-sm font-semibold tracking-tight">Polygence GraphRAG</h1>
        </div>
        <p className="text-[11px] text-slate-400 leading-snug">
          Pharmacogenomic & anesthesia risk reasoning
        </p>
      </div>

      <div className="p-3 border-b border-slate-800/60">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search drugs, genes, phenotypes…"
            className="w-full pl-8 pr-2 py-2 text-xs rounded-md bg-slate-900/60 border border-slate-700/60 focus:border-blue-500/60 outline-none placeholder:text-slate-500"
          />
        </div>
        {hits.length > 0 && (
          <ul className="mt-2 max-h-56 overflow-auto rounded-md border border-slate-800/60 bg-slate-900/70 text-xs">
            {hits.map((h) => (
              <li key={h.id}>
                <button
                  onClick={() => { loadFor(h.id); setQ(""); setHits([]); }}
                  className="w-full text-left px-2.5 py-1.5 hover:bg-slate-800/70 flex items-center justify-between gap-2"
                >
                  <span className="truncate">{h.label}</span>
                  <span className="chip">{NODE_LABELS[h.type] ?? h.type}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <button className="btn w-full mt-2 justify-center" onClick={loadSeed}>
          <RefreshCw size={12}/> Load anesthesia seed
        </button>
      </div>

      <div className="p-3 overflow-y-auto flex-1 space-y-4">
        <Section title="Node types">
          {(Object.keys(NODE_LABELS) as NodeType[]).map((t) => (
            <Toggle
              key={t}
              checked={filters.nodeTypes[t]}
              onChange={() => toggleNodeType(t)}
              label={NODE_LABELS[t]}
              dot={dotColorFor(t)}
            />
          ))}
        </Section>

        <Section title="Edge types">
          {(Object.keys(EDGE_LABELS) as EdgeType[]).map((t) => (
            <Toggle
              key={t}
              checked={filters.edgeTypes[t]}
              onChange={() => toggleEdgeType(t)}
              label={EDGE_LABELS[t]}
              dot={t === "linked_to_risk" || t === "can_trigger" ? "#ef4444" : "#64748b"}
            />
          ))}
        </Section>

        <Section title="Evidence level">
          <div className="flex flex-wrap gap-1.5">
            {EVIDENCE_LEVELS.map((lv) => (
              <button
                key={lv}
                onClick={() => toggleEvidenceLevel(lv)}
                className={
                  "px-2 py-1 rounded-md text-[10px] font-medium border transition-colors " +
                  (filters.evidenceLevels[lv]
                    ? "bg-blue-500/20 border-blue-500/40 text-blue-200"
                    : "bg-slate-900/60 border-slate-700/50 text-slate-500")
                }
              >{lv}</button>
            ))}
          </div>
        </Section>

        <Section title="Display">
          <Toggle
            checked={filters.collapseVariants}
            onChange={() => setFilters({ collapseVariants: !filters.collapseVariants })}
            label="Collapse variants"
          />
          <label className="text-[11px] text-slate-400 block mt-2">
            Max nodes: <span className="text-slate-200">{filters.maxNodes}</span>
            <input
              type="range" min={20} max={300} step={10}
              value={filters.maxNodes}
              onChange={(e) => setFilters({ maxNodes: Number(e.target.value) })}
              className="w-full mt-1 accent-blue-500"
            />
          </label>
        </Section>

        <button className="btn w-full justify-center" onClick={resetFilters}>
          <FilterIcon size={12}/> Reset filters
        </button>
      </div>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1.5">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Toggle({
  checked, onChange, label, dot,
}: { checked: boolean; onChange: () => void; label: string; dot?: string }) {
  return (
    <label className="flex items-center gap-2 text-xs cursor-pointer text-slate-300 hover:text-white">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="accent-blue-500 h-3 w-3 rounded"
      />
      {dot && <span style={{ background: dot }} className="w-2 h-2 rounded-full" />}
      <span>{label}</span>
    </label>
  );
}

function dotColorFor(t: NodeType) {
  return {
    drug: "#3b82f6", gene: "#ec4899", variant_cluster: "#f97316",
    drug_class: "#a855f7", phenotype: "#eab308",
  }[t];
}
