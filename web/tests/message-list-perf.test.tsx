/**
 * Long-conversation render smoke — roadmap R12.
 *
 * A 500-message session must not mount 500 message rows. MessageList
 * bounds what it renders through two layers, each pinned by its own
 * test here:
 *
 * 1. a logical window (useMessageWindow): only the most recent 50
 *    messages enter the row list — asserted directly against the hook
 *    via renderHook, free of any layout dependency;
 * 2. DOM virtualization (useVirtualizer): the component smoke below
 *    verifies the mounted stubs are a small subset of the window (jsdom
 *    lacks layout, so the exact window position inside the viewport is
 *    environment-dependent — only the bound is asserted).
 *
 * MessageItem is stubbed so the timing measures the list layer —
 * windowing + virtualizer + store reads — and not the markdown
 * pipeline, which has its own tests (message-item.test.tsx). Elapsed
 * time is logged, never asserted: wall clock varies per machine and a
 * hard threshold would flake.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, render, renderHook, screen } from "@testing-library/react";
import type { Message } from "../src/types/ipc";

vi.mock("../src/components/chat/MessageItem", () => ({
  MessageItem: ({ message }: { message: Message }) => (
    <div data-testid={`message-stub-${message.id}`} />
  ),
}));

import { MessageList } from "../src/components/chat/MessageList";
import { useMessageWindow } from "../src/lib/useMessageWindow";
import { useChat } from "../src/stores";

const TOTAL = 500;
const WINDOW = 50;

/**
 * Advance N animation frames inside act() so rAF-scheduled work settles
 * before assertions (same helper as message-list.test.tsx).
 */
async function flushFrames(frames: number): Promise<void> {
  for (let i = 0; i < frames; i += 1) {
    await act(async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
    });
  }
}

function makeMessages(count: number): Message[] {
  const base = Date.now();
  return Array.from({ length: count }, (_, i) => {
    const role: Message["role"] = i % 3 === 0 ? "user" : i % 3 === 1 ? "assistant" : "tool";
    return {
      id: `m${i}`,
      role,
      text: `message ${i} ${"lorem ipsum ".repeat(6)}`,
      tool_name: role === "tool" ? "read_file" : undefined,
      streaming: false,
      created_at: base + i,
    } satisfies Message;
  });
}

/** Indices of the mounted message stubs, e.g. [466, 467, ...]. */
function mountedStubIndices(): number[] {
  return screen
    .queryAllByTestId(/^message-stub-/)
    .map((el) => Number(/message-stub-m(\d+)$/.exec(el.dataset.testid ?? "")?.[1] ?? -1))
    .filter((n) => n >= 0);
}

describe("MessageList long-conversation smoke", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  });

  it(`renders ${TOTAL} messages as a bounded window, not the whole history`, async () => {
    useChat.setState({ messages: makeMessages(TOTAL) });

    const t0 = performance.now();
    render(<MessageList />);
    await flushFrames(3);
    const elapsed = performance.now() - t0;

    const indices = mountedStubIndices();
    // The logical window only admits the newest 50 messages, so every
    // mounted stub must come from the tail of the history — and the
    // virtualizer mounts a small subset of even that window.
    expect(indices.length).toBeGreaterThanOrEqual(1);
    expect(indices.length).toBeLessThanOrEqual(WINDOW);
    expect(indices.length).toBeLessThan(TOTAL / 10);
    for (const index of indices) {
      expect(index).toBeGreaterThanOrEqual(TOTAL - WINDOW);
    }

    console.log(
      `[message-list-perf] ${TOTAL} messages: ${indices.length} rows mounted in ${elapsed.toFixed(0)}ms (jsdom, MessageItem stubbed)`,
    );
  });

  it(`useMessageWindow keeps a ${WINDOW}-message window and pages it open`, () => {
    const ids = Array.from({ length: TOTAL }, (_, i) => `m${i}`);
    const { result } = renderHook(() => useMessageWindow(ids, WINDOW));

    expect(result.current.visible).toHaveLength(WINDOW);
    expect(result.current.visible[0]).toBe(`m${TOTAL - WINDOW}`);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.hiddenCount).toBe(TOTAL - WINDOW);

    act(() => result.current.loadMore());

    expect(result.current.visible).toHaveLength(WINDOW * 2);
    expect(result.current.visible[0]).toBe(`m${TOTAL - WINDOW * 2}`);
    expect(result.current.hiddenCount).toBe(TOTAL - WINDOW * 2);
  });
});
