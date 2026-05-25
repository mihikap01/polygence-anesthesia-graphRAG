"use client";

import { useEffect, useMemo, useState } from "react";
import Fuse from "fuse.js";
import Link from "next/link";
import { Search, RefreshCw, Filter as FilterIcon, Info } from "lucide-react";
import { useStore } from "@/lib/store";
import type { SearchHit, NodeType, EdgeType } from "@/lib/graph/types";
import { fetchSearchIndex, fetchGraphForSeed, fetchSeedGraph } from "@/lib/graph/data-api";

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
    fetchSearchIndex().then(setAllHits);
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
    const g = await fetchGraphForSeed(id, 2);
    setGraph(g);
    setLoading(false);
  }

  async function loadSeed() {
    setLoading(true);
    setGraph(await fetchSeedGraph());
    setLoading(false);
  }

  return (
    <aside className="w-72 shrink-0 h-full flex flex-col card border-r border-y-0 border-l-0">
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <h1 className="text-sm font-semibold tracking-tight text-foreground">Polygence GraphRAG</h1>
        </div>
        <p className="text-[11px] text-muted-foreground leading-snug">
          Pharmacogenomic & anesthesia risk reasoning
        </p>
        <Link
          href="/about"
          className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/15 transition-colors"
          title="About this project"
        >
          <Info size={12}/> About this project
        </Link>
      </div>

      <div className="p-3 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search drugs, genes, phenotypes…"
            className="w-full pl-9 pr-3 py-2 text-xs rounded-full bg-card border border-border focus:border-primary/50 outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 focus:ring-offset-background placeholder:text-muted-foreground/70 shadow-soft"
          />
        </div>
        {hits.length > 0 && (
          <ul className="mt-2 max-h-56 overflow-auto rounded-2xl border border-border bg-card text-xs shadow-soft">
            {hits.map((h) => (
              <li key={h.id}>
                <button
                  onClick={() => { loadFor(h.id); setQ(""); setHits([]); }}
                  className="w-full text-left px-3 py-2 hover:bg-accent flex items-center justify-between gap-2 text-foreground"
                >
                  <span className="truncate">{h.label}</span>
                  <span className="chip">{NODE_LABELS[h.type] ?? h.type}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <button className="btn btn-primary w-full mt-2 justify-center" onClick={loadSeed}>
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
              dot={t === "linked_to_risk" || t === "can_trigger" ? "#dc2626" : "#94a3b8"}
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
                  "px-2.5 py-1 rounded-full text-[10px] font-medium border transition-colors " +
                  (filters.evidenceLevels[lv]
                    ? "bg-primary/15 border-primary/40 text-primary"
                    : "bg-card border-border text-muted-foreground hover:bg-accent")
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
          <label className="text-[11px] text-muted-foreground block mt-2">
            Max nodes: <span className="text-foreground">{filters.maxNodes}</span>
            <input
              type="range" min={20} max={300} step={10}
              value={filters.maxNodes}
              onChange={(e) => setFilters({ maxNodes: Number(e.target.value) })}
              className="w-full mt-1 accent-primary"
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
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold mb-1.5">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Toggle({
  checked, onChange, label, dot,
}: { checked: boolean; onChange: () => void; label: string; dot?: string }) {
  return (
    <label className="flex items-center gap-2 text-xs cursor-pointer text-foreground/80 hover:text-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="accent-primary h-3.5 w-3.5 rounded"
      />
      {dot && <span style={{ background: dot }} className="w-2 h-2 rounded-full" />}
      <span>{label}</span>
    </label>
  );
}

function dotColorFor(t: NodeType) {
  return {
    drug: "#2563eb", gene: "#db2777", variant_cluster: "#ea580c",
    drug_class: "#7c3aed", phenotype: "#ca8a04",
  }[t];
}
