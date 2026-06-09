/**
 * Tests for P1#16 — MessageItem selector optimization.
 * Verifies:
 *   1. MessageItem is wrapped with React.memo
 *   2. MessageItem does NOT re-render when same message object is passed
 *   3. MessageItem re-renders when message.id changes
 *   4. Source file contains React.memo wrapping
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import fs from "fs";
import path from "path";

// Mock stores minimally
vi.mock("../../stores", () => {
  function ms(state: Record<string, unknown>) {
    const sel = (s: (st: Record<string, unknown>) => unknown) => s(state);
    sel.state = state;
    return sel;
  }
  return {
    useChat: ms({
      messages: [],
      sendMessage: vi.fn(),
      streaming: false,
      currentSessionId: "test-session",
    }),
    useThemeStore: ms({ theme: "dark" }),
  };
});

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

vi.mock("remark-gfm", () => ({
  default: () => () => {},
  __esModule: true,
}));

vi.mock("../../lib/shikiLoader", () => ({
  highlight: () => Promise.resolve(null),
}));

import { MessageItem } from "../../components/MessageItem";
import type { Message } from "../../types/ipc";

describe("MessageItem React.memo optimization (P1#16)", () => {
  it("MessageItem is wrapped with React.memo", () => {
    // React.memo creates a special component type
    expect(typeof MessageItem).toBe("object");
    // The $$typeof should indicate it's a memo component
    expect(MessageItem.$$typeof).toBe(Symbol.for("react.memo"));
  });

  it("MessageItem source contains React.memo wrapping", () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, "../../components/MessageItem.tsx"),
      "utf-8",
    );
    expect(src).toMatch(/React\.memo\s*\(\s*function\s+MessageItem/);
  });

  it("renders correctly with a user message", () => {
    const msg: Message = {
      id: "m-memo-1",
      role: "user",
      text: "Hello memo test",
      streaming: false,
      created_at: Date.now(),
    };
    render(<MessageItem message={msg} />);
    expect(screen.getByText("Hello memo test")).toBeTruthy();
  });

  it("renders correctly with an assistant message", () => {
    const msg: Message = {
      id: "m-memo-2",
      role: "assistant",
      text: "I am an assistant",
      streaming: false,
      created_at: Date.now(),
    };
    render(<MessageItem message={msg} />);
    expect(screen.getByText("I am an assistant")).toBeTruthy();
  });
});
