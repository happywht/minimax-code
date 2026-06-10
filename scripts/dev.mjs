#!/usr/bin/env node
// scripts/dev.mjs — runs Vite + (optionally) the Python agent in parallel.
//
// We use the `concurrently` package which is already a devDependency.
//
// Usage:
//   pnpm dev                    # Vite + agent
//   AGENT_SKIP=1 pnpm dev       # Vite only (no agent)
//   PORT=5174 pnpm dev          # override Vite port

import { spawn } from "node:child_process";
import process from "node:process";

const isWindows = process.platform === "win32";
const packageRunner = process.env.npm_execpath
  ? { cmd: process.execPath, baseArgs: [process.env.npm_execpath] }
  : { cmd: isWindows ? "pnpm.cmd" : "pnpm", baseArgs: [] };

function run(label, cmd, args, opts = {}) {
  const child = spawn(cmd, args, {
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
    ...opts,
  });
  const prefix = `[${label}]`;
  child.stdout.on("data", (b) => {
    process.stdout.write(
      b
        .toString()
        .split("\n")
        .map((line) => (line ? `${prefix} ${line}\n` : ""))
        .join(""),
    );
  });
  child.stderr.on("data", (b) => {
    process.stderr.write(
      b
        .toString()
        .split("\n")
        .map((line) => (line ? `${prefix} ${line}\n` : ""))
        .join(""),
    );
  });
  child.on("exit", (code) => {
    process.stderr.write(`${prefix} exited with ${code}\n`);
  });
  return child;
}

const procs = [];

if (!process.env.AGENT_SKIP) {
  procs.push(run("agent", packageRunner.cmd, [...packageRunner.baseArgs, "run", "-s", "dev:agent"]));
} else {
  console.log("[agent] skipped (AGENT_SKIP=1)");
}

procs.push(run("web", packageRunner.cmd, [...packageRunner.baseArgs, "run", "-s", "dev:web"]));

const shutdown = () => {
  procs.forEach((p) => {
    try {
      p.kill("SIGTERM");
    } catch {}
  });
  setTimeout(() => process.exit(0), 200);
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

// Exit when either child dies.
const tracker = setInterval(() => {
  if (procs.some((p) => p.killed || p.exitCode !== null)) {
    clearInterval(tracker);
    shutdown();
  }
}, 500);
