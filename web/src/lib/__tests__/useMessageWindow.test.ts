/**
 * Tests for P1#11 — message list windowed rendering.
 * Verifies:
 *   1. useMessageWindow returns only the last N messages by default
 *   2. hasMore is true when messages exceed window size
 *   3. loadMore expands the visible window
 *   4. hiddenCount is correct
 *   5. When messages fit within window, hasMore is false
 *   6. Total count reflects the full array length
 */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMessageWindow } from "../useMessageWindow";

// Helper: generate N numbered messages
function makeMessages(n: number): number[] {
  return Array.from({ length: n }, (_, i) => i);
}

describe("useMessageWindow", () => {
  it("returns all messages when count <= windowSize", () => {
    const { result } = renderHook(() => useMessageWindow(makeMessages(30), 50));
    expect(result.current.visible).toHaveLength(30);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.hiddenCount).toBe(0);
    expect(result.current.total).toBe(30);
  });

  it("returns only last windowSize messages when count > windowSize", () => {
    const { result } = renderHook(() => useMessageWindow(makeMessages(120), 50));
    expect(result.current.visible).toHaveLength(50);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.hiddenCount).toBe(70);
    // Should be the LAST 50 messages (indices 70..119)
    expect(result.current.visible[0]).toBe(70);
    expect(result.current.visible[49]).toBe(119);
  });

  it("loadMore expands the window by one page", () => {
    const { result } = renderHook(() => useMessageWindow(makeMessages(120), 50));
    expect(result.current.visible).toHaveLength(50);

    act(() => {
      result.current.loadMore();
    });

    // Now showing 100 messages (2 pages × 50)
    expect(result.current.visible).toHaveLength(100);
    expect(result.current.hiddenCount).toBe(20);
    expect(result.current.hasMore).toBe(true);
    // First visible message should be 20 (index 0 hidden)
    expect(result.current.visible[0]).toBe(20);
  });

  it("loadMore twice shows all messages", () => {
    const { result } = renderHook(() => useMessageWindow(makeMessages(120), 50));

    act(() => {
      result.current.loadMore();
    });
    act(() => {
      result.current.loadMore();
    });

    expect(result.current.visible).toHaveLength(120);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.hiddenCount).toBe(0);
  });

  it("handles empty message array", () => {
    const { result } = renderHook(() => useMessageWindow([], 50));
    expect(result.current.visible).toHaveLength(0);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.total).toBe(0);
  });

  it("default windowSize is 50", () => {
    // Exactly 51 messages → 1 hidden
    const { result } = renderHook(() => useMessageWindow(makeMessages(51)));
    expect(result.current.visible).toHaveLength(50);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.hiddenCount).toBe(1);
  });
});
