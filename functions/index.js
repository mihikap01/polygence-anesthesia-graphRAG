// Cloud Function proxy → Google Gemini. The browser POSTs to /api/llm
// (rewritten to this function). The Gemini key lives in a Firebase secret
// so it never enters the static bundle.
//
// One-time setup before first deploy:
//   scripts/set-gemini-key.sh
//   (or: firebase functions:secrets:set GEMINI_KEY, paste when prompted)
//
// Rotate the same way; redeploy after.
//
// Gemini exposes an OpenAI-compatible /chat/completions endpoint, so the
// request shape is identical to other OpenAI-style providers.

const { onRequest } = require("firebase-functions/v2/https");
const { defineSecret } = require("firebase-functions/params");
const { logger } = require("firebase-functions");

const GEMINI_KEY = defineSecret("GEMINI_KEY");

const GEMINI_URL =
  "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions";
const GEMINI_MODEL = "gemini-2.5-flash";

// Hostnames we accept (Origin header). Add custom domains here later.
const ALLOWED_ORIGINS = new Set([
  "https://polygence-pubmed-graphrag.web.app",
  "https://polygence-pubmed-graphrag.firebaseapp.com",
  "http://localhost:3000", // dev
  "http://localhost:5000", // firebase emulator
]);

// Best-effort per-IP rate limit. Cloud Functions can run on multiple instances,
// so this caps within a single warm instance — fine for abuse smoothing, not
// a hard global limit. If you need that, add Firestore-backed counters.
const WINDOW_MS = 60_000;
const MAX_PER_WINDOW = 20;
const hits = new Map(); // ip -> [timestamps]

function rateLimited(ip) {
  const now = Date.now();
  const arr = (hits.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > MAX_PER_WINDOW;
}

function setCors(req, res) {
  const origin = req.headers.origin || "";
  if (ALLOWED_ORIGINS.has(origin)) {
    res.set("Access-Control-Allow-Origin", origin);
    res.set("Vary", "Origin");
  }
  res.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.set("Access-Control-Allow-Headers", "Content-Type");
  res.set("Access-Control-Max-Age", "3600");
}

exports.ask = onRequest(
  {
    secrets: [GEMINI_KEY],
    region: "us-central1",
    cors: false,
    maxInstances: 10,
    // Public so the Firebase Hosting rewrite (/api/llm → ask) can reach it.
    // Abuse is mitigated by the Origin check + per-IP rate limit below.
    invoker: "public",
  },
  async (req, res) => {
    setCors(req, res);
    if (req.method === "OPTIONS") {
      res.status(204).send("");
      return;
    }
    if (req.method !== "POST") {
      res.status(405).json({ error: "method not allowed" });
      return;
    }

    const origin = req.headers.origin || "";
    if (origin && !ALLOWED_ORIGINS.has(origin)) {
      logger.warn("rejected origin", { origin });
      res.status(403).json({ error: "forbidden origin" });
      return;
    }

    const ip =
      (req.headers["x-forwarded-for"] || "").toString().split(",")[0].trim() ||
      req.ip ||
      "unknown";
    if (rateLimited(ip)) {
      res.status(429).json({ error: "rate limit" });
      return;
    }

    const body = req.body || {};
    const { systemPrompt, userPrompt, temperature } = body;
    if (typeof systemPrompt !== "string" || typeof userPrompt !== "string") {
      res.status(400).json({ error: "systemPrompt and userPrompt required" });
      return;
    }
    if (systemPrompt.length + userPrompt.length > 64_000) {
      res.status(413).json({ error: "prompt too large" });
      return;
    }

    const t0 = Date.now();
    let r;
    try {
      r = await fetch(GEMINI_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${GEMINI_KEY.value()}`,
        },
        body: JSON.stringify({
          model: GEMINI_MODEL,
          temperature: typeof temperature === "number" ? temperature : 0.2,
          max_tokens: 1024,
          messages: [
            { role: "system", content: systemPrompt },
            { role: "user", content: userPrompt },
          ],
        }),
      });
    } catch (e) {
      logger.error("gemini fetch failed", e);
      res.status(502).json({ error: "upstream unreachable" });
      return;
    }

    if (!r.ok) {
      const text = await r.text().catch(() => "");
      logger.warn("gemini non-ok", { status: r.status, text: text.slice(0, 300) });
      res.status(502).json({ error: `gemini ${r.status}` });
      return;
    }
    const j = await r.json();
    const text = j?.choices?.[0]?.message?.content ?? "";
    res.json({
      provider: "gemini",
      text,
      model: GEMINI_MODEL,
      duration_ms: Date.now() - t0,
    });
  }
);
