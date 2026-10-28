import { test, expect } from "@playwright/test";
import { AGENT_BASE } from "./runtime-config";

test("startup offline rejects writes and reconnect loads the real session", async ({ page, request }) => {
  const created = await request.post(`${AGENT_BASE}/rpc`, {
    data: { jsonrpc: "2.0", id: 1, method: "session.create", params: { title: "Offline recovery evidence" } },
  });
  const body = await created.json();
  expect(body.error).toBeUndefined();
  const sessionId = body.result.session.id as string;
  let rpcPosts = 0;
  await page.route("**/rpc", async (route) => {
    rpcPosts += 1;
    await route.continue();
  });
  await page.route("**/health", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByTestId("connection-banner")).toContainText("连接出错");
  const offline = await page.evaluate(async () => {
    // @ts-expect-error Vite exposes source modules to the browser test.
    const { ipc } = await import("/src/ipc/client.ts");
    try {
      await ipc.request("agent.send_message", { content: "must not run" });
      return { rejected: false, mock: ipc.isMock };
    } catch {
      return { rejected: true, mock: ipc.isMock };
    }
  });
  expect(offline).toEqual({ rejected: true, mock: false });
  expect(rpcPosts).toBe(0);
  await page.unroute("**/health");
  await page.getByTestId("connection-retry").click();
  await expect.poll(() => page.evaluate(async (id) => {
    // @ts-expect-error Vite exposes source modules to the browser test.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    return useSessionStore.getState().sessions.some((session: { id: string }) => session.id === id);
  }, sessionId), { timeout: 15_000 }).toBe(true);
  expect(rpcPosts).toBeGreaterThan(0);
});
