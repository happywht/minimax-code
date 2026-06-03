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
  await page.goto("/");

  // The composer should be visible.
  const input = page.getByPlaceholder(/Ask MiniMax anything/);
  await expect(input).toBeVisible({ timeout: 10_000 });

  // Type "@" to open the picker; the mock backend has 'general' seeded.
  await input.click();
  await input.fill("@");
  const picker = page.getByTestId("message-input-agent-picker");
  await expect(picker).toBeVisible({ timeout: 5_000 });
  const generalItem = page.getByTestId(
    "message-input-agent-picker-item-general",
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
