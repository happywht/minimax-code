/**
 * Playwright globalSetup — boot the Python agent and wait for /health.
 *
 * - Spawns `uv run python -m minimax_code` from the agent/ directory
 *   with PYTHONUNBUFFERED and a clean data dir.
 * - Polls GET /health for up to 30 seconds.
 * - Writes the child pid + spawn timestamp to
 *   e2e/.runtime/agent.json so globalTeardown can kill it.
 * - Tolerates failure: if the agent never comes up, specs still run
 *   (the web client auto-falls-back to mock mode). smoke-agent-rpc
 *   will then FAIL — which is the correct signal that the agent is
 *   not running. We log a warning rather than throwing.
 */
import { spawn, ChildProcess } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const AGENT_PORT = 8765;
const HEALTH_URL = `http://127.0.0.1:${AGENT_PORT}/health`;
const POLL_MS = 500;
const DEADLINE_MS = 30_000;

// Where to put runtime state for the teardown to read.
const RUNTIME_DIR = resolve(__dirname, ".runtime");
const PID_FILE = join(RUNTIME_DIR, "agent.json");

// Repo-root relative (this file is in e2e/, repo root is ..).
const REPO_ROOT = resolve(__dirname, "..");
const AGENT_DIR = join(REPO_ROOT, "agent");

function isWindows() {
  return process.platform === "win32";
}

function spawnAgent(): ChildProcess {
  const uvCmd = isWindows() ? "uv.exe" : "uv";
  const args = ["run", "python", "-m", "minimax_code"];
  const child = spawn(uvCmd, args, {
    cwd: AGENT_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
      MINIMAX_CODE_LOG_LEVEL: "WARNING",
      // Empty API key -> mock LLM (matches tests/e2e/smoke_*.py convention).
      MINIMAX_API_KEY: "",
      // Force a unique data dir so e2e runs don't pollute the user's dev DB.
      MINIMAX_CODE_DATA_DIR: join(RUNTIME_DIR, "data"),
    },
  });
  // Forward child output prefixed so users can see boot logs.
  const prefix = "[agent-boot]";
  child.stdout?.on("data", (b: Buffer) => {
    const lines = b.toString().split("\n");
    for (const line of lines) {
      if (line) console.log(`${prefix} ${line}`);
    }
  });
  child.stderr?.on("data", (b: Buffer) => {
    const lines = b.toString().split("\n");
    for (const line of lines) {
      if (line) console.error(`${prefix} ${line}`);
    }
  });
  return child;
}

async function waitForHealth(): Promise<boolean> {
  const deadline = Date.now() + DEADLINE_MS;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(HEALTH_URL, {
        method: "GET",
        signal: AbortSignal.timeout(2_000),
      });
      if (resp.ok) {
        const body = (await resp.json()) as { ok?: boolean };
        if (body.ok) {
          console.log(`[global-setup] agent healthy at ${HEALTH_URL}`);
          return true;
        }
      }
    } catch {
      // Agent not up yet; keep polling.
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  return false;
}

export default async function globalSetup(): Promise<void> {
  mkdirSync(RUNTIME_DIR, { recursive: true });
  console.log(`[global-setup] cwd: ${process.cwd()}`);
  console.log(`[global-setup] spawning agent in ${AGENT_DIR}`);

  const child = spawnAgent();
  const ok = await waitForHealth();
  if (!ok) {
    console.warn(
      `[global-setup] WARN: agent did not respond at ${HEALTH_URL} within ${DEADLINE_MS}ms.\n` +
        `  Web client will fall back to mock mode; smoke-agent-rpc will fail.\n` +
        `  (This is expected if uv is not installed or agent deps are missing.)`,
    );
  }

  writeFileSync(
    PID_FILE,
    JSON.stringify(
      {
        pid: child.pid,
        spawnAt: new Date().toISOString(),
        healthy: ok,
      },
      null,
      2,
    ),
  );
  console.log(`[global-setup] wrote ${PID_FILE} (pid=${child.pid})`);
}
