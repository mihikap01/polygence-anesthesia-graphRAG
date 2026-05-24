// Provider-agnostic LLM facade.  Picks the backend at runtime based on the
// LLM_PROVIDER env var so the routes stay clean:
//
//   LLM_PROVIDER=claude-cli   → spawn `claude -p` (requires Claude Code installed)
//   LLM_PROVIDER=openai       → use the OpenAI SDK (requires OPENAI_API_KEY)
//   LLM_PROVIDER=none (or unset and no OPENAI_API_KEY)
//                             → return null so the route falls back to its
//                               deterministic raw-context template
//
// Returning `{ provider: "none" }` instead of throwing keeps the app usable
// without any credentials — useful for demo videos and testing the retrieval
// layer in isolation.

import { askClaudeCli } from "./claude-cli";
import { getOpenAI, MODEL as OPENAI_MODEL } from "./openai";

export type Provider = "claude-cli" | "openai" | "none";

export interface AskInput {
  systemPrompt: string;
  userPrompt: string;
  temperature?: number;
}

export interface AskOutput {
  provider: Provider;
  text: string;
  cost_usd?: number;
  duration_ms?: number;
}

function chooseProvider(): Provider {
  const explicit = (process.env.LLM_PROVIDER || "").toLowerCase();
  if (explicit === "claude-cli" || explicit === "openai" || explicit === "none") {
    return explicit as Provider;
  }
  // Auto-pick: OpenAI if a key is set, else none.
  if (process.env.OPENAI_API_KEY) return "openai";
  return "none";
}

export async function ask(input: AskInput): Promise<AskOutput | null> {
  const provider = chooseProvider();
  if (provider === "none") return null;

  if (provider === "claude-cli") {
    const t0 = Date.now();
    const r = await askClaudeCli(input.userPrompt, {
      systemPrompt: input.systemPrompt,
    });
    return {
      provider,
      text: r.text,
      cost_usd: r.cost_usd,
      duration_ms: r.duration_ms ?? (Date.now() - t0),
    };
  }

  // openai
  const client = getOpenAI();
  if (!client) return null;
  const t0 = Date.now();
  const resp = await client.chat.completions.create({
    model: OPENAI_MODEL,
    temperature: input.temperature ?? 0.2,
    messages: [
      { role: "system", content: input.systemPrompt },
      { role: "user", content: input.userPrompt },
    ],
  });
  return {
    provider,
    text: resp.choices[0]?.message?.content ?? "",
    duration_ms: Date.now() - t0,
  };
}

export function currentProvider(): Provider {
  return chooseProvider();
}
