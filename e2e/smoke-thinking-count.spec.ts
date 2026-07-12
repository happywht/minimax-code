/**
 * v0.3.0 thinking_count e2e — closes the last v0.2.0 known limitation.
 *
 * The v0.2.0 README listed "thinkingCount reserved in MessageMetadata
 * but never emitted" as a known limitation. v0.3.0 wires the channel
 * end-to-end: the agent's `agent.message_chunk` event now carries
 * `metadata.thinking_count` on the trailing chunk, the chat store
 * keeps the snapshot on the message, and `MessageItem`'s summary
 * row reads it.
 *
 * Spec: send a prompt, wait for the assistant message to render
 * its per-turn summary, assert the "思考 N 次" line shows N >= 1.
 *
 * The mock LLM is used (MINIMAX_API_KEY="" in the global setup) so
 * `thinking_count` is always 1 per call. With the real API the
 * counter is whatever the upstream `usage.thinking_tokens` reports
 * (could be 0 if the model didn't think at all) — but the mock path
 * is what every e2e run sees.
 */
import { test, expect } from "@playwright/test";

test("chat: assistant message shows the thinking_count summary", async ({ page }) => {
  await page.goto("/");

  // Wait for the composer to be ready (same selector as smoke-chat).
  const input = page.getByPlaceholder(/Ask MiniMax anything/);
  await expect(input).toBeVisible({ timeout: 10_000 });

  // Send a deterministic prompt so the assistant message is easy
  // to find on the page.
  const prompt = "ping from thinking_count e2e";
  await input.fill(prompt);
  await input.press("Enter");

  // The assistant message bubble should appear. We use a generous
  // timeout because the mock LLM streams 16 chars every 5ms — a
  // long string takes ~50ms, plus the WS hop, plus React renders.
  const assistant = page.locator("[data-testid='message-assistant']").first();
  await expect(assistant).toBeVisible({ timeout: 10_000 });

  // The summary row is rendered above the markdown body. It carries
  // a data-testid of `message-summary-<id>` (added in v0.2.0 for
  // the per-turn summary feature).
  const summary = assistant.locator("[data-testid^='message-summary-']");
  await expect(summary).toBeVisible({ timeout: 10_000 });
  await expect(summary).toContainText(/思考\s*[1-9]\d*\s*次/);

  // The summary line must show "思考 N 次" with N >= 1. The mock
  // LLM always reports thinking_count=1 per call (v0.3.0 spec).
  const text = (await summary.textContent()) ?? "";
  expect(text).toMatch(/思考\s*\d+\s*次/);
  const match = text.match(/思考\s*(\d+)\s*次/);
  expect(match).not.toBeNull();
  const n = Number(match?.[1] ?? "0");
  expect(n).toBeGreaterThanOrEqual(1);
});
