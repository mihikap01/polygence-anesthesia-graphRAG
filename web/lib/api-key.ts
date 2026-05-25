"use client";

// User-supplied API key, stored in localStorage.
// Only used in the BYOK (static Firebase) build — local dev never touches this.

export type ByokProvider = "openai" | "anthropic";

const KEY_STORAGE = "polygence:llm:key";
const PROVIDER_STORAGE = "polygence:llm:provider";

export interface StoredKey {
  provider: ByokProvider;
  key: string;
}

export function isBYOK(): boolean {
  return process.env.NEXT_PUBLIC_BYOK === "1";
}

/** Heuristic: pick provider from key prefix when not explicitly set. */
export function detectProvider(key: string): ByokProvider | null {
  if (key.startsWith("sk-ant-")) return "anthropic";
  if (key.startsWith("sk-")) return "openai";
  return null;
}

export function getStoredKey(): StoredKey | null {
  if (typeof window === "undefined") return null;
  const key = window.localStorage.getItem(KEY_STORAGE);
  if (!key) return null;
  const provider =
    (window.localStorage.getItem(PROVIDER_STORAGE) as ByokProvider | null) ||
    detectProvider(key);
  if (!provider) return null;
  return { provider, key };
}

export function setStoredKey(provider: ByokProvider, key: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY_STORAGE, key);
  window.localStorage.setItem(PROVIDER_STORAGE, provider);
}

export function clearStoredKey(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(KEY_STORAGE);
  window.localStorage.removeItem(PROVIDER_STORAGE);
}
