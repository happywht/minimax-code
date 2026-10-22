import { expect, test } from "@playwright/test";
import { AGENT_BASE, E2E_AUTH_TOKEN } from "./runtime-config";

// These tests deliberately do not inherit the authenticated API fixture.
test.use({ extraHTTPHeaders: {} });

test("unauthenticated RPC and hostile-origin requests are rejected", async ({ request }) => {
  const body = { jsonrpc: "2.0", id: 1, method: "session.list", params: {} };
  expect((await request.post(`${AGENT_BASE}/rpc`, { data: body })).status()).toBe(401);
  expect((await request.post(`${AGENT_BASE}/rpc`, {
    data: body, headers: { Origin: "https://evil.example", Authorization: `Bearer ${E2E_AUTH_TOKEN}` },
  })).status()).toBe(403);
});

test("trusted browser bootstraps authentication and sends read-only plan mode", async ({ page }) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/问 MiniMax 任何问题/);
  await expect(input).toBeVisible();
  await page.getByTestId("execution-mode").selectOption("plan");
  const requestPromise = page.waitForRequest((request) => request.url().endsWith("/rpc") &&
    request.postDataJSON()?.method === "agent.send_message");
  await input.fill("Plan this task without editing files");
  await input.press("Enter");
  const request = await requestPromise;
  expect(request.postDataJSON().params.mode).toBe("plan");
  expect(request.headers().authorization?.startsWith("Bearer ")).toBe(true);
  await expect(page.getByTestId("message-assistant").first()).toBeVisible({ timeout: 15000 });
});
