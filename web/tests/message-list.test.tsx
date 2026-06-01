/**
 * Tests for the MessageList — verifies the empty state renders and
 * that message bubbles are listed.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
