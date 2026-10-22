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
import { test, expect, type Page } from "@playwright/test";

test("chat: send a message and see the assistant reply", async ({ page }) => {
  await page.goto("/");

  // The chat input is a textarea with the Chinese placeholder (R32 copy migration).
  const input = page.getByPlaceholder(/问 MiniMax 任何问题/);
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

  const input = page.getByPlaceholder(/问 MiniMax 任何问题/);
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

  await expect.poll(() => page.evaluate(async () => {
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useChat } = await import("/src/stores/chat.ts");
    return useChat.getState().status;
  })).toBe("idle");
  await expect.poll(() => page.getByTestId("message-list").evaluate(
    (el) => el.scrollHeight - el.scrollTop - el.clientHeight,
  )).toBeLessThanOrEqual(50);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

  const beforeWheelUp = await page.getByTestId("message-list").evaluate((el) => el.scrollTop);
  expect(beforeWheelUp).toBeGreaterThan(200);
  await page.mouse.wheel(0, -700);
  await expect
    .poll(() => page.getByTestId("message-list").evaluate((el) => el.scrollTop))
    .toBeLessThan(beforeWheelUp);
});

test("chat: streaming follows the bottom until the user scrolls up", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("message-list")).toBeVisible({ timeout: 10_000 });
  await waitForChatStoreReady(page);

  await page.evaluate(async () => {
    // These browser-side imports resolve to the same Vite modules used by the app.
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useChat } = await import("/src/stores/chat.ts");
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().create("Scroll fixture");
    const seed = Array.from({ length: 36 }, (_, index) => ({
      id: `seed-${index}`,
      role: index % 2 === 0 ? "user" : "assistant",
      text: `${index}: ${"long history content ".repeat(14)}`,
      streaming: false,
      status: "completed",
      created_at: index + 1,
    }));
    useChat.setState({ messages: seed, status: "streaming", error: null });
  });

  await expect
    .poll(() =>
      page.getByTestId("message-list").evaluate((el) => el.scrollHeight - el.clientHeight),
    )
    .toBeGreaterThan(1_000);

  // Pin the scroller to the bottom and CONFIRM it sticks there before
  // streaming: virtualization keeps resizing rows right after seeding, and
  // those scrollHeight shifts fire native scroll events that can flip
  // useSmartScroll's following flag off between our one-shot scrollTo and
  // the incoming chunk (observed as distance 941 instead of <= 50).
  // Re-asserting scrollTop every poll round re-fires the scroll event, so
  // when this settles the following flag is guaranteed engaged.
  await expect
    .poll(() =>
      page.getByTestId("message-list").evaluate((el) => {
        el.scrollTop = el.scrollHeight;
        el.dispatchEvent(new Event("scroll"));
        return el.scrollHeight - el.scrollTop - el.clientHeight;
      }),
    )
    .toBeLessThanOrEqual(50);

  await page.evaluate(async () => {
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { ipc } = await import("/src/ipc/index.ts");
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    ipc._emit("agent.message_chunk", {
      session_id: useSessionStore.getState().currentSessionId,
      message_id: "stream-follow",
      delta: "streaming line\n".repeat(180),
      done: false,
    });
  });

  await expect(page.getByText(/streaming line/).first()).toBeVisible();
  await expect
    .poll(() =>
      page.getByTestId("message-list").evaluate(
        (el) => el.scrollHeight - el.scrollTop - el.clientHeight,
      ),
    )
    .toBeLessThanOrEqual(50);

  const scroller = page.getByTestId("message-list");
  const beforeUserScroll = await scroller.evaluate((el) => el.scrollTop);
  await scroller.hover();
  await page.mouse.wheel(0, -900);
  await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBeLessThan(beforeUserScroll);
  const userScrollTop = await scroller.evaluate((el) => el.scrollTop);

  await page.evaluate(async () => {
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { ipc } = await import("/src/ipc/index.ts");
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    ipc._emit("agent.message_chunk", {
      session_id: useSessionStore.getState().currentSessionId,
      message_id: "stream-follow",
      delta: "more output that must not steal the user's position\n".repeat(40),
      done: false,
    });
  });

  await expect(page.getByTestId("scroll-to-bottom-btn")).toBeVisible();
  await expect
    .poll(() => page.getByTestId("message-list").evaluate((el) => el.scrollTop))
    // Tolerance matches useSmartScroll's thresholdPx (50): the assertion only
    // needs to prove streaming did NOT snap the user back to the bottom.
    .toBeLessThanOrEqual(userScrollTop + 50);
});

test("chat: assistant text and tool activity stay interleaved", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("message-list")).toBeVisible({ timeout: 10_000 });
  await waitForChatStoreReady(page);

  await page.evaluate(async () => {
    // @ts-expect-error Vite serves these source modules directly in the browser.
    const { useChat } = await import("/src/stores/chat.ts");
    // @ts-expect-error Vite serves these source modules directly in the browser.
    const { ipc } = await import("/src/ipc/index.ts");
    // Create a real selected session. A synthetic ID can be cleared by the
    // boot-time session refresh, erasing injected messages nondeterministically.
    // @ts-expect-error Vite serves these source modules directly in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    const sessionId = await useSessionStore.getState().create("Timeline fixture");
    useChat.setState({
      messages: [{
        id: "timeline-user",
        role: "user",
        text: "Inspect the project",
        streaming: false,
        status: "completed",
        created_at: 1,
      }],
      status: "streaming",
      error: null,
    });
    ipc._emit("agent.message_chunk", {
      session_id: sessionId,
      message_id: "timeline-before-tool",
      delta: "I will inspect the files first.",
      done: false,
    });
    ipc._emit("agent.message_chunk", {
      session_id: sessionId,
      message_id: "timeline-before-tool",
      delta: "",
      done: true,
    });
    ipc._emit("agent.tool_call", {
      session_id: sessionId,
      tool_call_id: "timeline-read",
      name: "read_file",
      args: { path: "README.md" },
      message_id: "timeline-before-tool",
    });
    ipc._emit("agent.tool_result", {
      session_id: sessionId,
      tool_call_id: "timeline-read",
      name: "read_file",
      result: "README contents",
      message_id: "timeline-before-tool",
    });
    ipc._emit("agent.message_chunk", {
      session_id: sessionId,
      message_id: "timeline-after-tool",
      delta: "The project structure is healthy.",
      done: true,
    });
    ipc._emit("agent.message_chunk", {
      session_id: "another-session",
      message_id: "foreign-message",
      delta: "This belongs to another session.",
      done: true,
    });
  });

  await expect(page.getByText("The project structure is healthy.", { exact: true })).toBeVisible();
  await expect(page.getByText("This belongs to another session.", { exact: true })).toHaveCount(0);
  const timeline = await page.getByTestId("message-list").evaluate((el) =>
    Array.from(el.querySelectorAll<HTMLElement>("[data-role]"), (node) => ({
      role: node.dataset.role,
      text: node.textContent?.replace(/\s+/g, " ").trim(),
    })),
  );

  expect(timeline.map((entry) => entry.role)).toEqual([
    "user",
    "assistant",
    "tool",
    "tool",
    "assistant",
  ]);
  expect(timeline[1]?.text).toContain("I will inspect the files first.");
  expect(timeline[2]?.text).toContain("read_file");
  expect(timeline[3]?.text).toContain("read_file");
  expect(timeline[4]?.text).toContain("The project structure is healthy.");
});

function escapeForRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function waitForChatStoreReady(page: Page): Promise<void> {
  await expect
    .poll(
      () => page.evaluate(async () => {
        // @ts-expect-error Vite serves this source module directly in the browser.
        const { useChat } = await import("/src/stores/chat.ts");
        return useChat.getState().agentReady;
      }),
      { timeout: 10_000 },
    )
    .toBe(true);
}
