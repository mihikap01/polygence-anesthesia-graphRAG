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
  const [provider, setProvider] = useState<ByokProvider>("anthropic");
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-slate-700/60 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800/60">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <KeyRound size={14} className="text-blue-400" />
            Your LLM API key
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-200"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-3 text-xs text-slate-300">
          <p className="leading-relaxed">
            This demo runs Explain and Chat with your own key — it's stored only
            in your browser's <code className="text-slate-100">localStorage</code> and
            sent directly to the provider. We never see it.
          </p>

          <div className="flex gap-2">
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
          </div>

          <div>
            <label className="block text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
              API key
            </label>
            <input
              autoFocus
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && save()}
              placeholder={
                provider === "anthropic" ? "sk-ant-…" : "sk-…"
              }
              className="w-full px-3 py-2 text-xs font-mono rounded-md bg-slate-950/60 border border-slate-700/60 focus:border-blue-500/60 outline-none placeholder:text-slate-600"
            />
            <p className="mt-1.5 text-[10px] text-slate-500">
              Get one at{" "}
              {provider === "anthropic" ? (
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 hover:underline inline-flex items-center gap-0.5"
                >
                  console.anthropic.com <ExternalLink size={9} />
                </a>
              ) : (
                <a
                  href="https://platform.openai.com/api-keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 hover:underline inline-flex items-center gap-0.5"
                >
                  platform.openai.com <ExternalLink size={9} />
                </a>
              )}
            </p>
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-slate-800/60">
            {hasExisting ? (
              <button
                onClick={clear}
                className="text-[11px] text-slate-400 hover:text-red-400 inline-flex items-center gap-1"
              >
                <Trash2 size={11} /> Clear stored key
              </button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-xs rounded-md text-slate-300 hover:bg-slate-800/60"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={!key.trim()}
                className="px-3 py-1.5 text-xs rounded-md bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white font-medium"
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
        "flex-1 px-3 py-1.5 text-xs rounded-md border transition-colors " +
        (active
          ? "bg-blue-500/20 border-blue-500/40 text-blue-200"
          : "bg-slate-900/60 border-slate-700/50 text-slate-400 hover:text-slate-200")
      }
    >
      {children}
    </button>
  );
}
