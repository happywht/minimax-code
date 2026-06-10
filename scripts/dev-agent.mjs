#!/usr/bin/env node
// scripts/dev-agent.mjs — runs the Python agent via uv, with PYTHONUNBUFFERED.
//
// Used by `pnpm dev:agent` and indirectly by `pnpm dev` (which spawns
// it via concurrently).

import { spawn } from "node:child_process";
import process from "node:process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const agentDir = path.join(repoRoot, "agent");

const isWindows = process.platform === "win32";
const uvCmd = isWindows ? "uv.exe" : "uv";

const child = spawn(
  uvCmd,
  ["run", "python", "-m", "minimax_code"],
  {
    cwd: agentDir,
    stdio: "inherit",
    shell: false,
    env: {
      ...process.env,
      MINIMAX_CODE_WORKSPACE: process.env.MINIMAX_CODE_WORKSPACE ?? repoRoot,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
    },
  },
);

child.on("exit", (code) => process.exit(code ?? 0));
process.on("SIGINT", () => child.kill("SIGINT"));
process.on("SIGTERM", () => child.kill("SIGTERM"));
