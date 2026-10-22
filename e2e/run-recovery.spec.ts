import { test, expect } from "@playwright/test";
import { AGENT_BASE } from "./runtime-config";

test("timeline recovers a completed offline run and survives a page reload", async ({ page, context, request }) => {
  const rpc = async (method: string, params: Record<string, unknown>) => {
    const response = await request.post(`${AGENT_BASE}/rpc`, {
      data: { jsonrpc: "2.0", id: method, method, params },
    });
    expect(response.ok()).toBe(true);
    const body = await response.json();
    expect(body.error).toBeUndefined();
    return body.result;
  };
  const created = await rpc("session.create", { title: "Durable recovery" });
  const sessionId = created.session.id as string;
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  const selectSession = async () => page.evaluate(async (id) => {
    // @ts-expect-error Vite exposes this source module in the test browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    // @ts-expect-error Vite exposes this source module in the test browser.
    const { useRunTimelineStore } = await import("/src/stores/runStore.ts");
    useSessionStore.setState({ currentSessionId: id });
    await useRunTimelineStore.getState().loadForSession(id);
  }, sessionId);
  await selectSession();
  await context.setOffline(true);
  const input = { session_id: sessionId, content: "offline durable recovery probe", idempotency_key: "offline-once" };
  const admitted = await rpc("run.start", input);
  expect(admitted.created).toBe(true);
  const retry = await rpc("run.start", input);
  expect(retry.created).toBe(false);
  expect(retry.run.id).toBe(admitted.run.id);
  await expect.poll(async () => (await rpc("run.get", { run_id: admitted.run.id })).run.status).toBe("completed");
  const receipt = await rpc("run.get", { run_id: admitted.run.id });
  expect(await rpc("agent.send_message", input)).toEqual(receipt.result);
  const { runs } = await rpc("run.list", { session_id: sessionId });
  expect(runs).toHaveLength(1);
  expect(runs[0].status).toBe("completed");
  const runId = runs[0].id as string;
  const saved = await rpc("run.steps", { run_id: runId });
  expect(saved.cursor).toBeGreaterThan(0);
  expect(saved.steps.length).toBeGreaterThan(0);
  await context.setOffline(false);
  const readRun = () => page.evaluate(async (id) => {
    // @ts-expect-error Vite exposes this source module in the test browser.
    const { useRunTimelineStore } = await import("/src/stores/runStore.ts");
    return useRunTimelineStore.getState().runs[id] ?? null;
  }, runId);
  await expect.poll(async () => (await readRun())?.status).toBe("completed");
  expect((await readRun()).steps).toEqual(saved.steps);
  await page.reload();
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await selectSession();
  await expect.poll(async () => (await readRun())?.status).toBe("completed");
  expect((await readRun()).steps).toEqual(saved.steps);
});
