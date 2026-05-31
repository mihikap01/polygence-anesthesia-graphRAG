import Link from "next/link";
import {
  Network, ShieldCheck, Eye, ArrowRight, Github, Linkedin, Layers,
} from "lucide-react";
import { Button } from "@/components/ui/Button";

export default function AboutPage() {
  return (
    <main className="relative min-h-screen overflow-x-hidden bg-background">
      {/* Soft ambient background — same recipe as silent-spaces landing */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(60% 50% at 50% 0%, hsl(195 50% 92%) 0%, transparent 60%), radial-gradient(40% 40% at 90% 100%, hsl(150 40% 92%) 0%, transparent 60%), radial-gradient(40% 40% at 0% 100%, hsl(40 60% 94%) 0%, transparent 60%)",
        }}
      />

      {/* Nav */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/" className="flex items-center gap-2" aria-label="Polygence GraphRAG home">
          <span className="flex h-9 w-9 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-soft">
            <Network size={18} aria-hidden />
          </span>
          <span className="text-lg font-semibold tracking-tight">
            Polygence GraphRAG
          </span>
        </Link>
        <Button asChild variant="outline" size="sm">
          <Link href="/">
            Open the graph
            <ArrowRight size={14} />
          </Link>
        </Button>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-3xl px-6 pb-12 pt-16 text-center sm:pt-20">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card/70 px-4 py-1.5 text-xs font-medium text-muted-foreground shadow-soft backdrop-blur">
          <span className="h-2 w-2 rounded-full bg-primary" aria-hidden />
          A transparent biomedical knowledge graph
        </span>

        <h1 className="mt-6 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl md:text-6xl">
          Reasoning about{" "}
          <span className="text-primary">drugs, genes,</span>{" "}
          and risk — out loud.
        </h1>

        <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
          A pharmacogenomic knowledge graph for anesthesia risk, paired with
          retrieval-augmented LLM reasoning you can inspect end-to-end.
        </p>
      </section>

      {/* Value props — three short cards */}
      <section className="mx-auto grid max-w-5xl gap-4 px-6 pb-20 sm:grid-cols-3">
        <ValueCard
          icon={<Network size={18} />}
          title="Real GraphRAG"
          body="Entity-linking, 1-hop neighbourhoods, BFS shortest paths — retrieval is a visible step, not a black box."
        />
        <ValueCard
          icon={<ShieldCheck size={18} />}
          title="Evidence-grounded"
          body="Built on PharmGKB clinical variants with evidence levels preserved. PMIDs are one click away."
        />
        <ValueCard
          icon={<Eye size={18} />}
          title="Transparent by design"
          body="Every answer ships with the exact prompt, retrieved subgraph, and token estimate."
        />
      </section>

      {/* Two prominent links — eval report and architecture */}
      <section className="mx-auto max-w-3xl px-6 pb-16 space-y-4">
        <a
          href="/eval-report"
          className="block rounded-3xl border border-border bg-card p-7 shadow-soft hover:shadow-gentle transition-shadow"
        >
          <div className="flex items-start gap-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <ShieldCheck size={18} />
            </span>
            <div className="flex-1">
              <div className="text-xs font-semibold uppercase tracking-wider text-primary mb-1">
                Research report
              </div>
              <h3 className="text-base font-semibold text-foreground">
                Does this graph layer actually help an LLM answer pharmacogenomics questions?
              </h3>
              <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                A blinded evaluation of subgraph RAG against a strong plain-text retriever and a
                no-context baseline. 187 held-out PharmGKB questions, four independent metric
                families, honest discussion of limitations. Result: mixed.
              </p>
              <div className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary">
                Read the report →
              </div>
            </div>
          </div>
        </a>

        <a
          href="/architecture"
          className="block rounded-3xl border border-border bg-card p-7 shadow-soft hover:shadow-gentle transition-shadow"
        >
          <div className="flex items-start gap-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <Layers size={18} />
            </span>
            <div className="flex-1">
              <div className="text-xs font-semibold uppercase tracking-wider text-primary mb-1">
                Code architecture
              </div>
              <h3 className="text-base font-semibold text-foreground">
                How the system is built — modules, data flow, file tree
              </h3>
              <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                A walkthrough of the codebase: how raw PharmGKB TSVs become a 3,213-node graph,
                how the live web app and eval framework consume it, and what happens for one
                example question end-to-end.
              </p>
              <div className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary">
                View the architecture →
              </div>
            </div>
          </div>
        </a>
      </section>

      {/* Founder — single short paragraph, inline */}
      <section className="mx-auto max-w-2xl px-6 pb-20 text-center">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Built by
        </h2>
        <p className="mt-3 text-lg font-semibold tracking-tight text-foreground">
          Mihika Pall
        </p>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          {/* EDIT: replace with your bio. */}
          A Polygence student researcher exploring how to make biomedical
          reasoning legible — turning thousands of PharmGKB records into a
          graph an LLM can actually reason over, with every step visible.
        </p>
        <div className="mt-5 flex justify-center gap-2">
          <a
            href="https://github.com/mihikap01"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <Github size={12} /> GitHub
          </a>
          <a
            href="#"
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <Linkedin size={12} /> LinkedIn
          </a>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl border-t border-border px-6 py-8 text-center text-xs text-muted-foreground">
        Built as part of the Polygence research mentorship program · Data from{" "}
        <a
          href="https://www.pharmgkb.org/"
          target="_blank"
          rel="noreferrer"
          className="text-primary hover:underline"
        >
          PharmGKB
        </a>
      </footer>
    </main>
  );
}

function ValueCard({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-3xl border border-border bg-card/80 p-6 shadow-soft backdrop-blur">
      <div
        className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary"
        aria-hidden
      >
        {icon}
      </div>
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{body}</p>
    </div>
  );
}
