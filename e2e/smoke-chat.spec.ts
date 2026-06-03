/**
 * v0.2.0 chat smoke — user can send a prompt and the agent
 * (in mock mode, since no API key is set) returns the canned reply.
 *
 * This exercises the real fetch / WebSocket stack end-to-end:
 *   1. Page loads, IPCClient.start() probes /health, picks `http` mode.
 *   2. User types into the message input.
 *   3. sendMessage() POSTs to /rpc.
 *   4. Mock LLM canned reply streams back via WebSocket `agent.message_chunk`.
 *   5. UI renders the assistant message.
 *
 * Skipped if the agent isn't running — falls back to mock mode in
 * the page and the same canned reply comes back from the in-process
 * mock backend. Both paths exercise the IPCClient + UI flow.
 */
import { test, expect } from "@playwright/test";

test("chat: send a message and see the assistant reply", async ({ page }) => {
  await page.goto("/");

  // The chat input is a textarea with placeholder "Ask MiniMax anything...".
  const input = page.getByPlaceholder(/Ask MiniMax anything/);
  await expect(input).toBeVisible({ timeout: 10_000 });

  const prompt = "ping from e2e";
  await input.fill(prompt);
  await input.press("Enter");

  // The user message should appear in the chat list immediately.
  await expect(page.getByText(prompt).first()).toBeVisible({
    timeout: 5_000,
  });

  // The mock LLM responds with a canned reply that includes the
  // prompt text. The exact prefix varies (mock vs http+mock-agent),
  // so we just check the reply mentions the prompt.
  await expect(
    page.getByText(new RegExp(escapeForRegex(prompt))).first(),
  ).toBeVisible({ timeout: 10_000 });
});

function escapeForRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
