#!/usr/bin/env node
// Build the Next.js app for Firebase Hosting (BYOK, static export).
// Temporarily moves app/api aside — Next can't statically prerender
// dynamic API routes, and they're not callable from a static host anyway.
// Always restores the directory, even if the build fails.

import { spawnSync } from "node:child_process";
import { existsSync, renameSync, cpSync, rmSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const apiDir = join(root, "app", "api");
const stashDir = join(root, "app", "_api_stash");

// 1. Copy data files into public/data so they're served at /data/*.json
const dataSrc = join(root, "..", "data");
const dataDst = join(root, "public", "data");
rmSync(dataDst, { recursive: true, force: true });
mkdirSync(dataDst, { recursive: true });
for (const f of ["graph.json", "seed_anesthesia.json", "search_index.json"]) {
  cpSync(join(dataSrc, f), join(dataDst, f));
}

// 2. Move API routes aside so Next doesn't try to prerender them.
let stashed = false;
if (existsSync(apiDir)) {
  renameSync(apiDir, stashDir);
  stashed = true;
}

let code = 1;
try {
  const env = { ...process.env, NEXT_PUBLIC_BYOK: "1" };
  const r = spawnSync("npx", ["next", "build"], { cwd: root, env, stdio: "inherit" });
  code = r.status ?? 1;
} finally {
  if (stashed && existsSync(stashDir)) {
    renameSync(stashDir, apiDir);
  }
}
process.exit(code);
