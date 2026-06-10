/**
 * Tests for the MessageList — verifies the empty state renders and
 * that message bubbles are listed.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MessageList } from "../src/components/MessageList";
import { useChat } from "../src/stores";

describe("MessageList", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  });

  it("shows the empty state when there are no messages", () => {
    render(<MessageList />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByText(/How can I help/)).toBeInTheDocument();
  });

  it("renders messages from the chat store", () => {
    useChat.setState({
      messages: [
        {
          id: "u1",
          role: "user",
          text: "hello",
          streaming: false,
          created_at: Date.now(),
        },
        {
          id: "a1",
          role: "assistant",
          text: "world",
          streaming: false,
          created_at: Date.now(),
        },
      ],
    });
    render(<MessageList />);
    expect(screen.getByTestId("message-list")).toBeInTheDocument();
    expect(screen.getByTestId("message-user")).toHaveTextContent("hello");
    expect(screen.getByTestId("message-assistant")).toHaveTextContent("world");
  });

  it("pauses auto-follow when user scrolls up and resumes on button click", async () => {
    useChat.setState({
      messages: [
        { id: "u1", role: "user", text: "hello", streaming: false, created_at: 1 },
        { id: "a1", role: "assistant", text: "world", streaming: false, created_at: 2 },
      ],
    });
    render(<MessageList />);

    const list = screen.getByTestId("message-list");
    Object.defineProperty(list, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(list, "clientHeight", { configurable: true, value: 400 });
    Object.defineProperty(list, "scrollTop", { configurable: true, writable: true, value: 400 });
    const scrollTo = vi.fn((opts: ScrollToOptions) => {
      list.scrollTop = Number(opts.top ?? 0);
    });
    Object.defineProperty(list, "scrollTo", { configurable: true, value: scrollTo });

    fireEvent.scroll(list);
    expect(await screen.findByTestId("scroll-to-bottom-btn")).toHaveTextContent("Latest");

    act(() => {
      useChat.setState((s) => ({
        messages: [
          ...s.messages,
          { id: "a2", role: "assistant", text: "new response", streaming: true, created_at: 3 },
        ],
      }));
    });

    await waitFor(() => {
      expect(screen.getByTestId("scroll-to-bottom-btn")).toHaveTextContent("1 new");
    });

    fireEvent.click(screen.getByTestId("scroll-to-bottom-btn"));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
  });
});
