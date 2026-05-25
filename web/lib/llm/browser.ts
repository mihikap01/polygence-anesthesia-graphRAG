"use client";

// Browser-side LLM provider. Two paths:
//
//   1. proxyAsk()     → POST /api/llm (Firebase Cloud Function → Gemini).
//                       Default when the user has not supplied a key.
//                       Gemini key stays on the server.
//
//   2. browserAsk()   → direct browser → OpenAI / Anthropic / DeepSeek /
//                       Gemini using the user's own key from localStorage
//                       (modal override).

import type { StoredKey } from "../api-key";

export interface BrowserAskInput {
  systemPrompt: string;
  userPrompt: string;
  temperature?: number;
  model?: string;
}

export interface BrowserAskOutput {
  provider: "openai" | "anthropic" | "deepseek" | "gemini";
  text: string;
  duration_ms: number;
  model: string;
}

const OPENAI_DEFAULT_MODEL = "gpt-4o-mini";
const ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5-20251001";
const DEEPSEEK_DEFAULT_MODEL = "deepseek-chat";
const GEMINI_DEFAULT_MODEL = "gemini-2.5-flash";

export async function browserAsk(
  stored: StoredKey,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  if (stored.provider === "openai") return askOpenAI(stored.key, input);
  if (stored.provider === "deepseek") return askDeepSeek(stored.key, input);
  if (stored.provider === "gemini") return askGemini(stored.key, input);
  return askAnthropic(stored.key, input);
}

async function askGemini(
  apiKey: string,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  const model = input.model || GEMINI_DEFAULT_MODEL;
  const t0 = Date.now();
  // Gemini's OpenAI-compatible endpoint.
  const r = await fetch(
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        temperature: input.temperature ?? 0.2,
        max_tokens: 1024,
        messages: [
          { role: "system", content: input.systemPrompt },
          { role: "user", content: input.userPrompt },
        ],
      }),
    }
  );
  if (!r.ok) {
    const errText = await safeReadError(r);
    throw new Error(`Gemini ${r.status}: ${errText}`);
  }
  const j = await r.json();
  return {
    provider: "gemini",
    text: j.choices?.[0]?.message?.content ?? "",
    duration_ms: Date.now() - t0,
    model,
  };
}

async function askDeepSeek(
  apiKey: string,
  input: BrowserAskInput
): Promise<BrowserAskOutput> {
  const model = input.model || DEEPSEEK_DEFAULT_MODEL;
  const t0 = Date.now();
  // DeepSeek's API is OpenAI-compatible.
  const r = await fetch("https://api.deepseek.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      temperature: input.temperature ?? 0.2,
      max_tokens: 1024,
      messages: [
        { role: "system", content: input.systemPrompt },
        { role: "user", content: input.userPrompt },
      ],
    }),
  });
  if (!r.ok) {
    const errText = await safeReadError(r);
    throw new Error(`DeepSeek ${r.status}: ${errText}`);
  }
  const j = await r.json();
  return {
    provider: "deepseek",
    text: j.choices?.[0]?.message?.content ?? "",
    duration_ms: Date.now() - t0,
    model,
  };
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

/** Call the Firebase Cloud Function proxy. The endpoint is rewritten in
 *  firebase.json from /api/llm → ask(). The Gemini key lives server-side. */
export async function proxyAsk(input: BrowserAskInput): Promise<BrowserAskOutput> {
  const t0 = Date.now();
  const r = await fetch("/api/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      systemPrompt: input.systemPrompt,
      userPrompt: input.userPrompt,
      temperature: input.temperature ?? 0.2,
    }),
  });
  if (!r.ok) {
    const errText = await safeReadError(r);
    throw new Error(`proxy ${r.status}: ${errText}`);
  }
  const j = await r.json();
  return {
    provider: j.provider ?? "gemini",
    text: j.text ?? "",
    model: j.model ?? "gemini-2.5-flash",
    duration_ms: j.duration_ms ?? Date.now() - t0,
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
