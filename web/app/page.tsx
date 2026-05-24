"use client";

import { useEffect } from "react";
import dynamic from "next/dynamic";
import LeftSidebar from "@/components/LeftSidebar";
import RightSidebar from "@/components/RightSidebar";
import { useStore } from "@/lib/store";

// Cytoscape touches `window` on construction, so load only on the client.
const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });

export default function Page() {
  const setGraph = useStore((s) => s.setGraph);
  const setLoading = useStore((s) => s.setLoading);
  const loading = useStore((s) => s.loading);

  useEffect(() => {
    setLoading(true);
    fetch("/api/graph?view=seed")
      .then((r) => r.json())
      .then((g) => { setGraph(g); setLoading(false); });
  }, [setGraph, setLoading]);

  return (
    <main className="flex h-screen w-screen overflow-hidden">
      <LeftSidebar />
      <section className="flex-1 relative min-w-0">
        <GraphCanvas />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm z-10">
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <span className="dots"><span/><span/><span/></span> Loading graph…
            </div>
          </div>
        )}
      </section>
      <RightSidebar />
    </main>
  );
}
