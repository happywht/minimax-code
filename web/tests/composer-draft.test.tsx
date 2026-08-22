/**
 * Tests for per-session composer drafts (P2-3) — the MessageInput
 * draft is scoped to the active session and mirrored to localStorage,
 * so switching sessions swaps drafts and a reload restores the text.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageInput } from "../src/components/chat/MessageInput";
import { useChat, useCodebaseStore, useSessionStore } from "../src/stores";

const DRAFT_KEY_S1 = "minimax-code:draft:s1";
const DRAFT_KEY_S2 = "minimax-code:draft:s2";

function type(text: string): void {
  fireEvent.change(screen.getByTestId("message-input-textarea"), {
    target: { value: text },
  });
}

function switchSession(id: string | null): void {
  act(() => {
    useSessionStore.setState({ currentSessionId: id });
  });
}

beforeEach(() => {
  window.localStorage.clear();
  useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  useCodebaseStore.getState().reset();
  useSessionStore.setState({ currentSessionId: null });
});

describe("MessageInput per-session drafts (P2-3)", () => {
  it("persists typing under the active session's key", () => {
    switchSession("s1");
    render(<MessageInput />);
    type("hello for s1");

    expect(window.localStorage.getItem(DRAFT_KEY_S1)).toBe("hello for s1");
  });

  it("swaps drafts on session switch without leaking text across sessions", () => {
    switchSession("s1");
    render(<MessageInput />);
    type("s1 draft");

    switchSession("s2");
    expect(screen.getByTestId("message-input-textarea")).toHaveValue("");
    type("s2 draft");
    expect(window.localStorage.getItem(DRAFT_KEY_S2)).toBe("s2 draft");

    switchSession("s1");
    expect(screen.getByTestId("message-input-textarea")).toHaveValue("s1 draft");
    // s2's text was never written into s1's slot.
    expect(window.localStorage.getItem(DRAFT_KEY_S1)).toBe("s1 draft");
  });

  it("restores the draft after a remount (reload semantics)", () => {
    switchSession("s1");
    const { unmount } = render(<MessageInput />);
    type("survives reload");
    unmount();

    render(<MessageInput />);
    expect(screen.getByTestId("message-input-textarea")).toHaveValue("survives reload");
  });

  it("removes the stored draft after sending", async () => {
    switchSession("s1");
    const user = userEvent.setup();
    render(<MessageInput />);
    await user.type(screen.getByTestId("message-input-textarea"), "bye{enter}");

    expect(window.localStorage.getItem(DRAFT_KEY_S1)).toBeNull();
  });

  it("keeps drafts session-scoped: no key is written when no session is active", () => {
    render(<MessageInput />);
    type("anonymous scratch");

    expect(window.localStorage.getItem("minimax-code:draft:null")).toBeNull();
    expect(Object.keys(window.localStorage).filter((k) => k.startsWith("minimax-code:draft:"))).toEqual([]);
  });
});
