#!/usr/bin/env node
// Deploy the Firebase build: static hosting + Cloud Function proxy.
//
// First-time setup (run once):
//   npm run set-secret:deepseek    # paste the DeepSeek key when prompted
//
// Then on every deploy:
//   npm run deploy:firebase

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = dirname(dirname(fileURLToPath(import.meta.url)));
const repoRoot = dirname(webDir);

function run(cmd, args, opts = {}) {
  const cwd = opts.cwd ?? repoRoot;
  console.log(`\n→ ${cmd} ${args.join(" ")}   (in ${cwd})`);
  const r = spawnSync(cmd, args, { cwd, stdio: "inherit", ...opts });
  if (r.status !== 0) {
    console.error(`\n✗ ${cmd} failed (exit ${r.status})`);
    process.exit(r.status ?? 1);
  }
}

// 1. Install function deps if missing.
const fnNodeModules = join(repoRoot, "functions", "node_modules");
if (!existsSync(fnNodeModules)) {
  console.log("installing function dependencies (one-time)…");
  run("npm", ["install"], { cwd: join(repoRoot, "functions") });
}

// 2. Build the static export.
run("npm", ["run", "build:firebase"], { cwd: webDir });

// 3. Deploy hosting + functions in one shot.
run("firebase", ["deploy", "--only", "hosting,functions"]);

console.log("\n✓ deployed → https://polygence-pubmed-graphrag.web.app");
