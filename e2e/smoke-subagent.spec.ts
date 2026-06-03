/**
 * v0.3.0 §2 sub-agent smoke — user can spawn a sub-agent from the
 * chat via the @-picker, watch the SubAgentPanel render a row,
 * and see a result card appear in the chat stream once the run
 * completes.
 *
 * The mock backend (no API key / no Python agent) fabricates a
 * 5-stage progress stream in ~400ms, so the result card is
 * expected to surface well within the 15s window in the design.
 */
import { test, expect } from "@playwright/test";

test("sub-agent: @-picker spawns a sub-agent and a result card appears", async ({
  page,
}) => {
  // Seed a "general" sub-agent via the real backend (this is the
  // one the picker hard-codes). In the mock path the agent is
  // already seeded by ``mockAgents``; against the live agent we
  // create it BEFORE the page mounts so the React
  // ``useEffect``-driven agent fetch sees the new row.
  const seedResp = await page.request.post("http://127.0.0.1:8765/rpc", {
    data: {
      jsonrpc: "2.0",
      id: "seed-general",
      method: "agent.create",
      params: {
        name: "general",
        system_prompt: "You are a general-purpose sub-agent.",
        enabled: true,
      },
    },
  });
  expect(seedResp.status()).toBe(200);
  const seedJson = (await seedResp.json()) as {
    result?: { agent?: { id?: string } };
  };
  const generalId = seedJson.result?.agent?.id;
  expect(generalId).toBeTruthy();

  await page.goto("/");

  // The composer should be visible.
  const input = page.getByPlaceholder(/Ask MiniMax anything/);
  await expect(input).toBeVisible({ timeout: 10_000 });

  // Type "@" to open the picker; the agent picker should show "general".
  await input.click();
  await input.fill("@");
  const picker = page.getByTestId("message-input-agent-picker");
  await expect(picker).toBeVisible({ timeout: 5_000 });
  const generalItem = page.getByTestId(
    `message-input-agent-picker-item-${generalId}`,
  );
  await expect(generalItem).toBeVisible();

  // Click the agent to spawn. The mock backend emits a 5-event
  // progress stream and the SubAgentPanel should render at least
  // one row within the 15s budget.
  await generalItem.click();
  await expect(
    page.getByTestId("sub-agent-panel-list"),
    "SubAgentPanel should render at least one row after the spawn",
  ).toBeVisible({ timeout: 10_000 });

  // A SubAgentResultCard should appear in the chat stream once the
  // mock backend's "completed" event lands. The mock runs 5 events
  // 80ms apart, so a 10s budget is comfortable.
  const resultCard = page.getByTestId("sub-agent-result");
  await expect(resultCard).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByTestId("sub-agent-result-status"),
  ).toHaveText(/completed|failed/);
});
