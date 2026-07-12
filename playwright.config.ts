import { defineConfig, devices } from "@playwright/test";
import { AGENT_BASE, AGENT_PORT, WEB_BASE, WEB_PORT } from "./e2e/runtime-config";

/**
 * Playwright config for MiniMax Code v0.2.0 e2e.
 *
 * Architecture:
 *   - globalSetup: spawns an isolated agent (`uv run python -m minimax_code`)
 *     in agent/ and polls /health until ready.
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
 * Dedicated default ports (15173/18765) prevent a test run from reusing or
 * mutating a developer's live product. CI can override both via
 * MINIMAX_CODE_E2E_WEB_PORT and MINIMAX_CODE_E2E_PORT.
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
    baseURL: WEB_BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  webServer: {
    command: `pnpm --filter @minimax/web exec vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
    url: WEB_BASE,
    env: { VITE_AGENT_URL: AGENT_BASE },
    reuseExistingServer: false,
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

export { AGENT_BASE, AGENT_PORT };
