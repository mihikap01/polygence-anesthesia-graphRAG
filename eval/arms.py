#!/usr/bin/env python3
"""
The four arms — each takes a question and returns an answer.

A0: question only
A1: question + RRF-fused top-8 chunks (BM25 + dense)
A2: question + anesthesia seed subgraph dumped flat
A3: question + structured GraphRAG packet (entities, neighborhoods, paths)

Each arm calls Claude (Opus 4.7) via the `claude` CLI with the same system
prompt as the live web app (web/lib/llm/prompts.ts:CHAT_SYSTEM), so the only
variable is the context.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Mirror web/lib/llm/prompts.ts — CHAT_SYSTEM
CHAT_SYSTEM = """You are a GraphRAG assistant answering questions about a pharmacogenomic knowledge graph.

Rules:
- Ground every claim in the supplied context. If the context does not support an answer, say so plainly.
- Cite PMIDs in-line as [PMID:xxxxxx] when you have them.
- Prefer the shortest reasoning path (drug → gene/variant → phenotype).
- Highlight CRITICAL / level 1A or 1B evidence when relevant.
- Keep answers under 200 words unless asked for more depth.
- Do not fabricate dosing recommendations or clinical advice."""

# A0 has no retrieval — explicitly empty context to keep prompt shape consistent
A0_NOCTX_PREFIX = "(No retrieved context provided. Answer from your own knowledge if confident; otherwise say so.)"


def _build_user_prompt(context_text: str, question: str) -> str:
    return f"""Context:
---
{context_text}
---

User question: {question}"""


@dataclass
class ArmAnswer:
    arm: str
    question_id: str
    answer: str
    duration_ms: int
    cost_usd: float = 0.0
    error: str | None = None
    context_chars: int = 0


def call_claude(system: str, user: str, model: str = "opus",
                timeout_s: int = 180) -> tuple[str, int, float, str | None]:
    args = [
        "claude", "-p", "--output-format", "json", "--model", model,
        "--append-system-prompt", system,
        "--disallowedTools",
        "Read Write Edit Bash WebSearch WebFetch Agent TaskCreate TaskUpdate TaskList",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(
            args, input=user, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "", int((time.time() - t0) * 1000), 0.0, "timeout"
    dur = int((time.time() - t0) * 1000)
    if r.returncode != 0:
        return "", dur, 0.0, f"exit {r.returncode}: {r.stderr[:200]}"
    try:
        j = json.loads(r.stdout)
    except Exception as e:
        return "", dur, 0.0, f"json parse: {e}"
    if j.get("is_error"):
        return "", dur, 0.0, f"claude is_error: {j.get('result', '')[:200]}"
    return j.get("result", ""), j.get("duration_ms", dur), float(j.get("total_cost_usd") or 0.0), None


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------

def run_a0(question_id: str, question: str, model: str = "opus") -> ArmAnswer:
    user = _build_user_prompt(A0_NOCTX_PREFIX, question)
    text, dur, cost, err = call_claude(CHAT_SYSTEM, user, model=model)
    return ArmAnswer("A0", question_id, text, dur, cost, err, len(A0_NOCTX_PREFIX))


def run_a1(question_id: str, question: str, model: str = "opus") -> ArmAnswer:
    from a1_retrieve import retrieve, render_a1_context
    chunks = retrieve(question)
    ctx = render_a1_context(chunks)
    user = _build_user_prompt(ctx, question)
    text, dur, cost, err = call_claude(CHAT_SYSTEM, user, model=model)
    return ArmAnswer("A1", question_id, text, dur, cost, err, len(ctx))


def run_a2(question_id: str, question: str, seed_graph: dict, model: str = "opus") -> ArmAnswer:
    from retrieve_py import render_a2_context
    ctx = render_a2_context(seed_graph)
    user = _build_user_prompt(ctx, question)
    text, dur, cost, err = call_claude(CHAT_SYSTEM, user, model=model)
    return ArmAnswer("A2", question_id, text, dur, cost, err, len(ctx))


def run_a3(question_id: str, question: str, gd, model: str = "opus") -> ArmAnswer:
    from retrieve_py import retrieve_for_question
    ctx, _ents, _nbh, _paths = retrieve_for_question(question, gd)
    user = _build_user_prompt(ctx, question)
    text, dur, cost, err = call_claude(CHAT_SYSTEM, user, model=model)
    return ArmAnswer("A3", question_id, text, dur, cost, err, len(ctx))
