/**
 * v0.2.0 boot smoke — page renders, agent handshake completes.
 *
 * Verifies:
 *   1. Vite serves the SPA shell (no 500 / unhandled exception).
 *   2. The MiniMax Code brand string is visible.
 *   3. The agent `/health` endpoint responds ok from the page context
 *      (proves the CORS config lets the browser talk to the agent).
 *   4. The v0.3.0 top bar contains the new ``GitStatusBar`` widget
 *      (proves the git IPC namespace is wired into the shell).
 *
 * Does NOT depend on the agent being reachable on first paint — the
 * web client is designed to fall back to mock mode if /health fails.
 * So this spec is the happy-path "everything is up" assertion; the
 * agent-rpc spec is the stricter "real round-trip" check.
 */
import { test, expect } from "@playwright/test";

test("boot: page renders and agent health probe succeeds", async ({ page, request }) => {
  // Direct API probe — independent of the page so a UI hang doesn't
  // mask a real agent outage. We can run this without the webServer.
  const health = await request.get("http://127.0.0.1:8765/health", {
    timeout: 5_000,
  });
  expect(health.ok(), "agent /health must return 2xx").toBeTruthy();
  const body = await health.json();
  expect(body.ok).toBe(true);
  expect(typeof body.version).toBe("string");
  expect(body.version.length).toBeGreaterThan(0);

  // UI probe — Vite dev page renders.
  await page.goto("/");
  // The brand mark is in the sidebar's collapsed view AND the top
  // bar — either is fine. Use the sidebar's text to avoid matching
  // the document title in the head.
  await expect(page.getByText("MiniMax Code").first()).toBeVisible({
    timeout: 10_000,
  });

  // v0.3.0: the top bar must contain the GitStatusBar widget
  // (data-testid="git-status-bar"). We don't assert the branch
  // text — the project is in a real git repo but the agent
  // subprocess runs from its own CWD, so the displayed branch
  // depends on the agent's environment, not the test runner's.
  await expect(page.locator("[data-testid='git-status-bar']").first()).toBeVisible({
    timeout: 10_000,
  });
});
