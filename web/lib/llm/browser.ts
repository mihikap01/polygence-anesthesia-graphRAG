"use client";

// Browser-side LLM provider. Calls OpenAI or Anthropic directly from the
// user's browser using the user's own API key (stored in localStorage).
// Only used in the BYOK static build — server routes stay untouched for local dev.

import type { StoredKey } from "../api-key";

export interface BrowserAskInput {
  systemPrompt: string;
  userPrompt: string;
  temperature?: number;
  model?: string;
}

export interface BrowserAskOutput {
  provider: "openai" | "anthropic";
  text: string;
  duration_ms: number;
  model: string;
}

const OPENAI_DEFAULT_MODEL = "gpt-4o-mini";
const ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5-20251001";

export async function browserAsk(
  stored: StoredKey,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  if (stored.provider === "openai") return askOpenAI(stored.key, input);
  return askAnthropic(stored.key, input);
}

async function askOpenAI(
  apiKey: string,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  const model = input.model || OPENAI_DEFAULT_MODEL;
  const t0 = Date.now();
  const r = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      temperature: input.temperature ?? 0.2,
      messages: [
        { role: "system", content: input.systemPrompt },
        { role: "user", content: input.userPrompt },
      ],
    }),
  });
  if (!r.ok) {
    const errText = await safeReadError(r);
    throw new Error(`OpenAI ${r.status}: ${errText}`);
  }
  const j = await r.json();
  return {
    provider: "openai",
    text: j.choices?.[0]?.message?.content ?? "",
    duration_ms: Date.now() - t0,
    model,
  };
}

async function askAnthropic(
  apiKey: string,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  const model = input.model || ANTHROPIC_DEFAULT_MODEL;
  const t0 = Date.now();
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model,
      max_tokens: 1024,
      temperature: input.temperature ?? 0.2,
      system: input.systemPrompt,
      messages: [{ role: "user", content: input.userPrompt }],
    }),
  });
  if (!r.ok) {
    const errText = await safeReadError(r);
    throw new Error(`Anthropic ${r.status}: ${errText}`);
  }
  const j = await r.json();
  const text =
    Array.isArray(j.content)
      ? j.content
          .filter((b: any) => b.type === "text")
          .map((b: any) => b.text)
          .join("\n")
      : "";
  return {
    provider: "anthropic",
    text,
    duration_ms: Date.now() - t0,
    model,
  };
}

async function safeReadError(r: Response): Promise<string> {
  try {
    const j = await r.json();
    return j?.error?.message || JSON.stringify(j);
  } catch {
    try {
      return await r.text();
    } catch {
      return r.statusText;
    }
  }
}
