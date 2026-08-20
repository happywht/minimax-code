/**
 * Production single-process mode e2e — the agent serves the built SPA
 * from web/dist behind one port (StaticFiles mount in http_server.py).
 *
 * Contract being pinned:
 *   1. GET /        → built index.html; the React app boots against its
 *                     same-origin agent (runtimeAgentBaseUrl falls back
 *                     to window.location.origin in production builds).
 *   2. GET /health  → JSON ok:true — routes keep precedence over the mount.
 *   3. POST /rpc    → JSON-RPC 2.0 answers on the same origin.
 *   4. WS   /ws     → same-origin WebSocket handshake upgrades.
 *   5. UI chat      → full round trip through the served SPA (mock LLM).
 *
 * global-setup guarantees web/dist exists before the agent spawns —
 * the mount is decided once at app build time.
 */
import { expect, test } from "@playwright/test";
import { AGENT_BASE } from "./runtime-config";

test.describe("production single-process mode", () => {
  test("serves the built SPA shell and boots the app same-origin", async ({ page }) => {
    const resp = await page.goto(AGENT_BASE);
    expect(resp?.status()).toBe(200);
    expect(resp?.headers()["content-type"]).toContain("text/html");

    await expect(page.getByTestId("app-root")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("sidebar-brand").first()).toBeVisible();
  });

  test("keeps /health and /rpc precedence over the SPA mount", async ({ request }) => {
    const health = await request.get(`${AGENT_BASE}/health`);
    expect(health.ok()).toBeTruthy();
    expect(await health.json()).toMatchObject({ ok: true });

    const rpc = await request.post(`${AGENT_BASE}/rpc`, {
      data: { jsonrpc: "2.0", id: 1, method: "session.list", params: {} },
    });
    expect(rpc.ok()).toBeTruthy();
    const body = await rpc.json();
    expect(body.jsonrpc).toBe("2.0");
    expect(body.result).toBeDefined();
  });

  test("upgrades a same-origin WebSocket at /ws", async ({ page }) => {
    await page.goto(AGENT_BASE);
    const opened = await page.evaluate((origin: string) => {
      return new Promise<boolean>((resolve) => {
        const ws = new WebSocket(origin.replace(/^http/i, "ws") + "/ws");
        const timer = setTimeout(() => resolve(false), 5_000);
        ws.onopen = () => {
          clearTimeout(timer);
          ws.close();
          resolve(true);
        };
        ws.onerror = () => {
          clearTimeout(timer);
          resolve(false);
        };
      });
    }, AGENT_BASE);
    expect(opened).toBe(true);
  });

  test("chats end-to-end through the served SPA", async ({ page }) => {
    test.setTimeout(45_000);
    await page.goto(AGENT_BASE);
    const input = page.getByPlaceholder(/Ask MiniMax anything/);
    await expect(input).toBeVisible({ timeout: 10_000 });

    const prompt = "hello from production mode";
    await input.fill(prompt);
    await input.press("Enter");

    // The user bubble appears immediately; the mock LLM then streams a
    // canned reply mentioning the prompt — proving the same-origin
    // fetch (/rpc) and WebSocket (message_chunk) paths both work.
    await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("message-assistant").first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
