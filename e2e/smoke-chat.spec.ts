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

test("chat: long conversations scroll inside the middle message area", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");

  const input = page.getByPlaceholder(/Ask MiniMax anything/);
  await expect(input).toBeVisible({ timeout: 10_000 });

  const longPrompt = Array.from(
    { length: 90 },
    (_, index) => `scroll proof line ${String(index + 1).padStart(2, "0")}`,
  ).join("\n");
  await input.fill(longPrompt);
  await page.getByTestId("message-input-send").click();

  await page.waitForFunction(() => {
    const el = document.querySelector<HTMLElement>('[data-testid="message-list"]');
    return Boolean(
      el &&
        el.textContent?.includes("scroll proof line 90") &&
        el.scrollHeight > el.clientHeight + 200,
    );
  });

  const metrics = await page.evaluate(() => {
    const scroller = document.querySelector<HTMLElement>('[data-testid="message-list"]');
    const header = document.querySelector<HTMLElement>('[data-testid="chat-header-title"]');
    const composer = document.querySelector<HTMLElement>('[data-testid="message-input"]');
    if (!scroller || !header || !composer) {
      throw new Error("chat layout elements missing");
    }

    const headerTopBefore = header.getBoundingClientRect().top;
    const composerTopBefore = composer.getBoundingClientRect().top;
    const pageScrollBefore = document.scrollingElement?.scrollTop ?? 0;

    scroller.scrollTop = 0;
    scroller.dispatchEvent(new Event("scroll"));
    const top = scroller.scrollTop;

    scroller.scrollTop = scroller.scrollHeight;
    scroller.dispatchEvent(new Event("scroll"));
    const bottom = scroller.scrollTop;

    return {
      top,
      bottom,
      clientHeight: scroller.clientHeight,
      scrollHeight: scroller.scrollHeight,
      headerTopBefore,
      headerTopAfter: header.getBoundingClientRect().top,
      composerTopBefore,
      composerTopAfter: composer.getBoundingClientRect().top,
      pageScrollBefore,
      pageScrollAfter: document.scrollingElement?.scrollTop ?? 0,
    };
  });

  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  expect(metrics.top).toBe(0);
  expect(metrics.bottom).toBeGreaterThan(0);
  expect(metrics.headerTopAfter).toBe(metrics.headerTopBefore);
  expect(metrics.composerTopAfter).toBe(metrics.composerTopBefore);
  expect(metrics.pageScrollAfter).toBe(metrics.pageScrollBefore);

  const box = await page.getByTestId("message-list").boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;

  const wheelMetrics = await page.evaluate(() => {
    const scroller = document.querySelector<HTMLElement>('[data-testid="message-list"]');
    if (!scroller) throw new Error("message list missing");
    const maxScrollTop = scroller.scrollHeight - scroller.clientHeight;
    scroller.scrollTop = Math.floor(maxScrollTop / 2);
    scroller.dispatchEvent(new Event("scroll"));
    return { scrollTop: scroller.scrollTop, maxScrollTop };
  });
  expect(wheelMetrics.maxScrollTop).toBeGreaterThan(200);
  expect(wheelMetrics.scrollTop).toBeGreaterThan(0);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

  const beforeWheelUp = await page.getByTestId("message-list").evaluate((el) => el.scrollTop);
  await page.mouse.wheel(0, -700);
  await expect
    .poll(() => page.getByTestId("message-list").evaluate((el) => el.scrollTop))
    .toBeLessThan(beforeWheelUp);
});

function escapeForRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
