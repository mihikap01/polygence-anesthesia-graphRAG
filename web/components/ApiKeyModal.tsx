"use client";

import { useEffect, useState } from "react";
import { KeyRound, Trash2, X, ExternalLink } from "lucide-react";
import {
  getStoredKey,
  setStoredKey,
  clearStoredKey,
  detectProvider,
  type ByokProvider,
} from "@/lib/api-key";

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

export default function ApiKeyModal({ open, onClose, onSaved }: Props) {
  const [provider, setProvider] = useState<ByokProvider>("gemini");
  const [key, setKey] = useState("");
  const [hasExisting, setHasExisting] = useState(false);

  useEffect(() => {
    if (!open) return;
    const existing = getStoredKey();
    if (existing) {
      setProvider(existing.provider);
      setKey("");
      setHasExisting(true);
    } else {
      setHasExisting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!key) return;
    const detected = detectProvider(key);
    if (detected) setProvider(detected);
  }, [key]);

  if (!open) return null;

  function save() {
    if (!key.trim()) return;
    setStoredKey(provider, key.trim());
    setKey("");
    onSaved?.();
    onClose();
  }

  function clear() {
    clearStoredKey();
    setHasExisting(false);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-3xl border border-border bg-card shadow-gentle animate-slide-up overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <KeyRound size={14} className="text-primary" />
            Use your own API key (optional)
          </div>
          <button
            onClick={onClose}
            className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-4 text-xs text-foreground/80">
          <p className="leading-relaxed">
            By default, this demo answers via our hosted Gemini backend.
            If you'd rather use your own key, paste it below — it's stored only
            in your browser's <code className="text-foreground bg-muted px-1 rounded">localStorage</code> and
            sent directly to the provider you choose.
          </p>

          <div className="grid grid-cols-2 gap-2">
            <ProviderBtn
              active={provider === "gemini"}
              onClick={() => setProvider("gemini")}
            >
              Gemini
            </ProviderBtn>
            <ProviderBtn
              active={provider === "anthropic"}
              onClick={() => setProvider("anthropic")}
            >
              Anthropic
            </ProviderBtn>
            <ProviderBtn
              active={provider === "openai"}
              onClick={() => setProvider("openai")}
            >
              OpenAI
            </ProviderBtn>
            <ProviderBtn
              active={provider === "deepseek"}
              onClick={() => setProvider("deepseek")}
            >
              DeepSeek
            </ProviderBtn>
          </div>

          <div>
            <label className="block text-[10px] uppercase tracking-wider text-muted-foreground font-semibold mb-1.5">
              API key
            </label>
            <input
              autoFocus
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && save()}
              placeholder={
                provider === "anthropic"
                  ? "sk-ant-…"
                  : provider === "gemini"
                  ? "AIza…"
                  : "sk-…"
              }
              className="w-full px-4 py-2.5 text-xs font-mono rounded-full bg-background border border-border focus:border-primary/60 outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 focus:ring-offset-card placeholder:text-muted-foreground/60 shadow-soft"
            />
            <p className="mt-2 text-[10px] text-muted-foreground">
              Get one at{" "}
              {provider === "anthropic" ? (
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  console.anthropic.com <ExternalLink size={9} />
                </a>
              ) : provider === "deepseek" ? (
                <a
                  href="https://platform.deepseek.com/api_keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  platform.deepseek.com <ExternalLink size={9} />
                </a>
              ) : provider === "gemini" ? (
                <a
                  href="https://aistudio.google.com/apikey"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  aistudio.google.com <ExternalLink size={9} />
                </a>
              ) : (
                <a
                  href="https://platform.openai.com/api-keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  platform.openai.com <ExternalLink size={9} />
                </a>
              )}
            </p>
          </div>

          <div className="flex items-center justify-between pt-3 border-t border-border">
            {hasExisting ? (
              <button
                onClick={clear}
                className="text-[11px] text-muted-foreground hover:text-red-600 inline-flex items-center gap-1"
              >
                <Trash2 size={11} /> Clear stored key
              </button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="px-4 py-2 text-xs font-medium rounded-full text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={!key.trim()}
                className="px-4 py-2 text-xs rounded-full bg-primary hover:brightness-105 active:brightness-95 disabled:bg-muted disabled:text-muted-foreground disabled:cursor-not-allowed text-primary-foreground font-medium shadow-soft transition-all"
              >
                Save key
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProviderBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "px-3 py-2 text-xs font-medium rounded-full border transition-colors " +
        (active
          ? "bg-primary/15 border-primary/40 text-primary"
          : "bg-card border-border text-muted-foreground hover:text-foreground hover:border-foreground/20")
      }
    >
      {children}
    </button>
  );
}
