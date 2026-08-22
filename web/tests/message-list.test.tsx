/**
 * Tests for the MessageList — verifies the empty state renders and
 * that message bubbles are listed.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MessageList } from "../src/components/chat/MessageList";
import { useChat } from "../src/stores";

/**
 * Advance N animation frames inside act() so rAF-scheduled work settles
 * before assertions. useSmartScroll schedules its follow/pause logic on
 * (nested) requestAnimationFrame callbacks — without flushing, assertions
 * can run before the callback fires, which flakes under load.
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

describe("MessageList", () => {
  beforeEach(() => {
    useChat.setState({
      messages: [],
      status: "idle",
      error: null,
      agentReady: false,
      loadingMessages: false,
    });
  });

  it("shows the empty state when there are no messages", () => {
    render(<MessageList />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByText(/今天想让我做什么/)).toBeInTheDocument();
  });

  it("shows the history skeleton while messages load instead of the empty state", () => {
    useChat.setState({ loadingMessages: true });
    render(<MessageList />);
    expect(screen.getByTestId("message-list-skeleton")).toBeInTheDocument();
    expect(screen.getByTestId("message-list-skeleton")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByTestId("empty-state")).not.toBeInTheDocument();
  });

  it("renders messages from the chat store", async () => {
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
    expect(screen.getByTestId("message-list")).toHaveClass("min-h-0", "overflow-y-auto");
    expect(screen.getByTestId("message-list").parentElement).toHaveClass("min-h-0", "overflow-hidden");
    expect(screen.getByTestId("message-window-row-message-u1")).toHaveStyle({
      contentVisibility: "auto",
    });
    expect(await screen.findByTestId("message-user", {}, { timeout: 3000 })).toHaveTextContent("hello");
    expect(await screen.findByTestId("message-assistant", {}, { timeout: 3000 })).toHaveTextContent("world");
  });

  it("renders tool messages as interleaved timeline rows", async () => {
    useChat.setState({
      messages: [
        { id: "a1", role: "assistant", text: "checking", streaming: false, created_at: 1 },
        {
          id: "t1",
          role: "tool",
          text: "read ok",
          tool_name: "read_file",
          streaming: false,
          created_at: 2,
        },
        {
          id: "t2",
          role: "tool",
          text: "grep ok",
          tool_name: "search",
          streaming: false,
          created_at: 3,
        },
        { id: "a2", role: "assistant", text: "done", streaming: false, created_at: 4 },
      ],
    });

    render(<MessageList />);

    expect(screen.queryByTestId("message-tool-group")).not.toBeInTheDocument();
    expect(screen.getByTestId("message-window-row-message-t1")).toBeInTheDocument();
    expect(screen.getByTestId("message-window-row-message-t2")).toBeInTheDocument();
    expect(await screen.findByText("read_file", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(await screen.findByText("search", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("collapses runs of 3+ tool messages into one group row", async () => {
    useChat.setState({
      messages: [
        { id: "u1", role: "user", text: "go", streaming: false, created_at: 1 },
        { id: "t1", role: "tool", text: "a", tool_name: "read_file", streaming: false, created_at: 2 },
        { id: "t2", role: "tool", text: "b", tool_name: "read_file", streaming: false, created_at: 3 },
        { id: "t3", role: "tool", text: "c", tool_name: "exec_command", streaming: false, created_at: 4 },
        { id: "a1", role: "assistant", text: "done", streaming: false, created_at: 5 },
      ],
    });
    render(<MessageList />);

    // One collapsed group row replaces the three individual cards.
    const group = await screen.findByTestId("message-tool-group");
    expect(screen.getByText("工具调用 × 3")).toBeInTheDocument();
    expect(screen.queryByTestId("message-window-row-message-t1")).not.toBeInTheDocument();
    expect(group).toHaveTextContent(/read_file ×2/);

    // Expanding reveals the individual tool cards.
    fireEvent.click(screen.getByRole("button", { name: /工具调用 × 3/ }));
    expect(await screen.findAllByTestId("message-tool")).toHaveLength(3);
  });

  it("keeps tool messages ungrouped while a search query is active", async () => {
    useChat.setState({
      messages: [
        { id: "t1", role: "tool", text: "alpha", tool_name: "read_file", streaming: false, created_at: 1 },
        { id: "t2", role: "tool", text: "beta", tool_name: "search", streaming: false, created_at: 2 },
        { id: "t3", role: "tool", text: "alpha", tool_name: "glob", streaming: false, created_at: 3 },
      ],
    });
    render(<MessageList searchQuery="alpha" />);
    expect(await screen.findByTestId("chat-search-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("message-tool-group")).not.toBeInTheDocument();
    expect(screen.getByTestId("message-window-row-message-t1")).toBeInTheDocument();
    expect(screen.getByTestId("message-window-row-message-t3")).toBeInTheDocument();
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
    expect(await screen.findByTestId("scroll-to-bottom-btn")).toHaveTextContent("最新");

    act(() => {
      useChat.setState((s) => ({
        messages: [
          ...s.messages,
          { id: "a2", role: "assistant", text: "new response", streaming: true, created_at: 3 },
        ],
      }));
    });
    // The pause path bumps newContentCount inside a rAF callback.
    await flushFrames(3);

    await waitFor(() => {
      expect(screen.getByTestId("scroll-to-bottom-btn")).toHaveTextContent("1 条新消息");
    });

    fireEvent.click(screen.getByTestId("scroll-to-bottom-btn"));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
  });

  it("keeps following the latest streaming output while already at the bottom", async () => {
    useChat.setState({
      messages: [
        { id: "u1", role: "user", text: "hello", streaming: false, created_at: 1 },
        { id: "a1", role: "assistant", text: "short", streaming: true, created_at: 2 },
      ],
    });
    render(<MessageList />);

    const list = screen.getByTestId("message-list");
    Object.defineProperty(list, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(list, "clientHeight", { configurable: true, value: 400 });
    Object.defineProperty(list, "scrollTop", { configurable: true, writable: true, value: 600 });
    const scrollTo = vi.fn((opts: ScrollToOptions) => {
      list.scrollTop = Number(opts.top ?? 0);
    });
    Object.defineProperty(list, "scrollTo", { configurable: true, value: scrollTo });

    fireEvent.scroll(list);

    act(() => {
      useChat.setState((s) => ({
        messages: s.messages.map((message) =>
          message.id === "a1"
            ? {
                ...message,
                text: `${message.text}\n${Array.from(
                  { length: 30 },
                  (_, index) => `streaming line ${index + 1}`,
                ).join("\n")}`,
              }
            : message,
        ),
      }));
    });
    // The follow path calls scrollToBottom from nested rAF callbacks.
    await flushFrames(3);

    await waitFor(() => {
      expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "auto" });
    });
    expect(screen.queryByTestId("scroll-to-bottom-btn")).not.toBeInTheDocument();
  });

  it("virtualizes the visible message window for long conversations", async () => {
    useChat.setState({
      messages: Array.from({ length: 80 }, (_, index) => ({
        id: `m${index}`,
        role: index % 2 === 0 ? "user" : "assistant",
        text: `message ${index}`,
        streaming: false,
        created_at: index,
      })),
    });

    render(<MessageList />);
    // Give the virtualizer its first measurement frame before asserting the window.
    await flushFrames(1);

    expect(screen.getByTestId("message-virtualizer")).toBeInTheDocument();
    expect(screen.queryByText("message 0")).toBeNull();
    await waitFor(() => {
      expect(screen.getAllByText(/message \d+/).length).toBeGreaterThan(0);
    });
    expect(screen.queryAllByTestId(/^message-window-row-/).length).toBeLessThan(50);
  });
});
