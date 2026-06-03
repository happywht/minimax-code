import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for MiniMax Code v0.2.0 e2e.
 *
 * Architecture:
 *   - globalSetup: spawns the agent (`uv run python -m minimax_code`)
 *     in agent/ as a real subprocess and polls /health until ready.
 *   - globalTeardown: taskkill /SIGTERM the agent pid written by setup.
 *   - webServer: starts `pnpm --filter @minimax/web dev` (Vite on 5173).
 *   - specs run against the running pair: browser → Vite → fetch/WS → agent.
 *
 * Why a globalSetup (not webServer for both):
 *   The agent's FastAPI app resolves `default_database_path()` relative
 *   to its CWD (agent/) and creates the data dir on first run. If we
 *   started it under `webServer`'s cwd (the repo root) the data dir
 *   would land at the wrong path and the smoke would be flaky on
 *   machines with read-only home directories.
 *
 * Why no Vite proxy for /rpc + /ws:
 *   The web client has CORS allow-listed to 127.0.0.1:5173 and
 *   127.0.0.1:8765. Vite proxies are nice but optional. Skipping
 *   them keeps the dev server config identical between `pnpm dev`
 *   and `pnpm test:e2e` — fewer drift surfaces.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  webServer: {
    command: "pnpm --filter @minimax/web dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});

/**
 * Spec helpers import these so they can talk to the agent directly
 * without going through the Vite proxy. Keep in sync with globalSetup
 * defaults (port 8765, host 127.0.0.1).
 */
export const AGENT_BASE = "http://127.0.0.1:8765";
export const AGENT_PORT = 8765;
