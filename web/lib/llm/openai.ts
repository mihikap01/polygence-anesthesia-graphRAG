// Single OpenAI client shared across API routes.  Returns null if the
// OPENAI_API_KEY env var is not set, so callers can fall back to deterministic
// template explanations and the app stays usable without a key.

import OpenAI from "openai";

let _client: OpenAI | null | undefined;

export function getOpenAI(): OpenAI | null {
  if (_client !== undefined) return _client;
  const key = process.env.OPENAI_API_KEY;
  if (!key) { _client = null; return null; }
  _client = new OpenAI({ apiKey: key });
  return _client;
}

export const MODEL = process.env.OPENAI_MODEL || "gpt-4o-mini";
