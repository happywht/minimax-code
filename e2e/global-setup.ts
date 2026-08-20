/**
 * Playwright globalSetup — boot the Python agent and wait for /health.
 *
 * - Spawns `uv run python -m minimax_code` from the agent/ directory
 *   with PYTHONUNBUFFERED and a clean data dir.
 * - Polls GET /health for up to 30 seconds.
 * - Writes the child pid + spawn timestamp to a port-scoped runtime
 *   directory so globalTeardown can kill only that process.
 * - Fails fast when the dedicated port is occupied or the isolated
 *   agent does not become healthy.
 */
import { spawn, ChildProcess, execSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { AGENT_BASE, AGENT_PORT, WEB_BASE } from "./runtime-config";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const HEALTH_URL = `${AGENT_BASE}/health`;
const POLL_MS = 500;
const DEADLINE_MS = 30_000;

// Where to put runtime state for the teardown to read.
const RUNTIME_DIR = resolve(__dirname, ".runtime", String(AGENT_PORT));
const PID_FILE = join(RUNTIME_DIR, "agent.json");
const DATA_DIR = join(RUNTIME_DIR, "data");
const WORKSPACE_DIR = join(RUNTIME_DIR, "workspace");

// Repo-root relative (this file is in e2e/, repo root is ..).
const REPO_ROOT = resolve(__dirname, "..");
const AGENT_DIR = join(REPO_ROOT, "agent");

function isWindows() {
  return process.platform === "win32";
}

/**
 * Seed a tiny workspace for the codebase indexer.
 *
 * The real agent/ directory contains ~500 source files and takes
 * close to two minutes to index on modest CI hardware. A small
 * synthetic workspace lets the codebase smoke test exercise the real
 * `codebase.build_index` and `codebase.search` handlers in a few
 * seconds without changing production defaults.
 */
function seedCodebaseWorkspace(): void {
  rmSync(WORKSPACE_DIR, { recursive: true, force: true });
  mkdirSync(WORKSPACE_DIR, { recursive: true });

  writeFileSync(
    join(WORKSPACE_DIR, "indexer.py"),
    `class CodebaseIndexer:
    """Walks a workspace and persists searchable chunks."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    async def build_index(self) -> dict:
        return {"status": "done", "files": 1}

    async def search(self, query: str) -> list:
        return []
`,
  );

  writeFileSync(
    join(WORKSPACE_DIR, "search.py"),
    `from indexer import CodebaseIndexer

async def search_codebase(indexer: CodebaseIndexer, query: str):
    """Search the indexed codebase for the given query."""
    return await indexer.search(query)
`,
  );

  writeFileSync(
    join(WORKSPACE_DIR, "handlers.py"),
    `async def handle_codebase_search(params: dict):
    query = params.get("query", "")
    return {"query": query, "results": []}
`,
  );
}

/**
 * Ensure web/dist exists before the agent spawns.
 *
 * The agent decides its StaticFiles mount once at app-build time
 * (`_web_dist_dir()` runs inside `build_app`), so a missing dist at
 * spawn means the production-mode specs would silently test nothing.
 * Building here keeps `pnpm test:e2e` self-sufficient; an existing
 * dist is reused as-is (force a rebuild by deleting web/dist).
 */
function ensureWebDist(): void {
  const distIndex = join(REPO_ROOT, "web", "dist", "index.html");
  if (existsSync(distIndex)) return;
  console.log("[global-setup] web/dist missing — running pnpm build");
  execSync("pnpm build", {
    cwd: REPO_ROOT,
    stdio: "inherit",
    shell: isWindows(),
    timeout: 180_000,
  });
  if (!existsSync(distIndex)) {
    throw new Error("pnpm build finished but web/dist/index.html is still missing");
  }
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
      MINIMAX_CODE_HTTP_PORT: String(AGENT_PORT),
      MINIMAX_CODE_CORS_ORIGINS: WEB_BASE,
      MINIMAX_CODE_FORCE_MOCK: "1",
      // Never read or spend a developer's credential-store API key.
      MINIMAX_API_KEY: "",
      PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring",
      // Force a unique data dir so e2e runs don't pollute the user's dev DB.
      MINIMAX_CODE_DATA_DIR: DATA_DIR,
      // Point the codebase indexer at a tiny synthetic workspace so the
      // codebase smoke test finishes quickly while still using real handlers.
      MINIMAX_CODE_WORKSPACE_DIR: WORKSPACE_DIR,
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

async function assertPortAvailable(): Promise<void> {
  await new Promise<void>((resolvePort, reject) => {
    const server = createServer();
    server.once("error", (error) => reject(error));
    server.listen(AGENT_PORT, "127.0.0.1", () => {
      server.close((error) => (error ? reject(error) : resolvePort()));
    });
  }).catch((error) => {
    throw new Error(
      `E2E agent port ${AGENT_PORT} is unavailable. Set MINIMAX_CODE_E2E_PORT to a free port.`,
      { cause: error },
    );
  });
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
  await assertPortAvailable();
  ensureWebDist();
  rmSync(DATA_DIR, { recursive: true, force: true });
  seedCodebaseWorkspace();
  mkdirSync(RUNTIME_DIR, { recursive: true });
  console.log(`[global-setup] cwd: ${process.cwd()}`);
  console.log(`[global-setup] spawning agent in ${AGENT_DIR}`);

  const child = spawnAgent();
  const ok = await waitForHealth();
  if (!ok) throw new Error(`E2E agent did not become healthy at ${HEALTH_URL}`);

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
