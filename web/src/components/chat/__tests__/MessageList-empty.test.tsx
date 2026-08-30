/**
 * Tests for the MessageList empty state (v1.7.1).
 *
 * The cold-start greeting and its suggestion cards were previously
 * hardcoded in the component — they must render from
 * ``strings.chat.emptyState`` so the copy has a single source of truth.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MessageList } from "../MessageList";
import { strings } from "../../../ui/strings";

vi.mock("../../../stores", () => ({
  useChat: (sel: (s: { messages: unknown[]; loadingMessages: boolean }) => unknown) =>
    sel({ messages: [], loadingMessages: false }),
  useSessionStore: (sel: (s: { currentSessionId: string | null }) => unknown) =>
    sel({ currentSessionId: "s1" }),
  useSubAgentStore: (sel: (s: { runs: unknown[] }) => unknown) => sel({ runs: [] }),
}));

afterEach(() => {
  cleanup();
});

describe("MessageList empty state", () => {
  it("renders the greeting from strings.chat.emptyState", () => {
    render(<MessageList />);
    expect(screen.getByText(strings.chat.emptyState.title)).toBeTruthy();
    expect(screen.getByText(strings.chat.emptyState.hint)).toBeTruthy();
  });

  it("renders one suggestion card per strings entry", () => {
    render(<MessageList />);
    for (const text of strings.chat.emptyState.suggestions) {
      expect(screen.getByText(text)).toBeTruthy();
    }
    // Suggestion click fills the composer via a DOM event; presence of all
    // cards is the contract pinned here.
    expect(strings.chat.emptyState.suggestions.length).toBeGreaterThanOrEqual(3);
  });
});
