// Provider that drives the `claude` CLI in headless / -p mode.
//
// Rationale: lets the app reuse the developer's existing Claude Code auth
// (no separate ANTHROPIC_API_KEY needed) and inherits Claude Code's
// tool-use machinery if we ever want to expose graph-navigation tools.
//
// Trade-offs to be aware of:
//   * Per-call cold-start is ~1.5 s before any inference happens.
//   * Each call has a baseline ~$0.01 cost from the CLI's own system-prompt
//     cache; with prompt caching on the rest amortises across turns.
//   * The CLI is single-user — concurrent requests share the same auth.
//
// The prompt is piped via stdin to avoid argv length limits on large
// graph-context packets.

import { spawn } from "node:child_process";

export interface ClaudeCliOptions {
  systemPrompt?: string;
  model?: string;            // "sonnet" | "haiku" | "opus" or full id
  allowedTools?: string[];   // e.g. ["Bash(echo *)"] – empty = none
  disallowedTools?: string[];
  timeoutMs?: number;
}

export interface ClaudeCliResult {
  text: string;
  cost_usd?: number;
  duration_ms?: number;
  num_turns?: number;
  session_id?: string;
  raw?: any;
}

export function isAvailable(): boolean {
  // Cheap check — the binary may not exist on the host. We don't actually
  // exec it here; the spawn call will surface ENOENT if it's missing.
  return process.env.LLM_PROVIDER === "claude-cli";
}

export async function askClaudeCli(
  userPrompt: string,
  opts: ClaudeCliOptions = {}
): Promise<ClaudeCliResult> {
  const model = opts.model || process.env.CLAUDE_MODEL || "haiku";
  const args: string[] = ["-p", "--output-format", "json", "--model", model];
  if (opts.systemPrompt) {
    args.push("--append-system-prompt", opts.systemPrompt);
  }
  // Forbid every tool we don't need — keeps Q&A turns to 1 and stops the
  // model from trying to Read/Bash on the dev's filesystem.
  const disallowed = opts.disallowedTools ?? [
    "Read", "Write", "Edit", "Bash", "WebSearch", "WebFetch",
    "Agent", "TaskCreate", "TaskUpdate", "TaskList",
  ];
  if (disallowed.length) {
    args.push("--disallowedTools", disallowed.join(" "));
  }
  if (opts.allowedTools?.length) {
    args.push("--allowedTools", opts.allowedTools.join(" "));
  }

  const timeoutMs = opts.timeoutMs ?? 60_000;
  return new Promise((resolve, reject) => {
    const child = spawn("claude", args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error(`claude CLI timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.on("data", (b) => (stdout += b.toString()));
    child.stderr.on("data", (b) => (stderr += b.toString()));
    child.on("error", (err: any) => {
      clearTimeout(timer);
      if (err.code === "ENOENT") {
        reject(new Error("`claude` binary not found on PATH. Install Claude Code or set LLM_PROVIDER=openai."));
      } else {
        reject(err);
      }
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        return reject(new Error(`claude CLI exited ${code}: ${stderr.slice(0, 400)}`));
      }
      try {
        const j = JSON.parse(stdout);
        if (j.is_error) return reject(new Error(j.result || "claude CLI returned is_error"));
        resolve({
          text: j.result ?? "",
          cost_usd: j.total_cost_usd,
          duration_ms: j.duration_ms,
          num_turns: j.num_turns,
          session_id: j.session_id,
          raw: j,
        });
      } catch (e: any) {
        reject(new Error(`Failed to parse claude CLI JSON: ${e.message}\n--stdout--\n${stdout.slice(0, 400)}`));
      }
    });

    child.stdin.write(userPrompt);
    child.stdin.end();
  });
}
