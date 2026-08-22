/**
 * v1.1.0 — budget-truncated assistant messages render a "continue"
 * affordance that delegates to the chat store's continueRun action.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageItem } from "../src/components/chat/MessageItem";
import { useChat } from "../src/stores";
import type { Message } from "../src/types/ipc";

vi.mock("mermaid", () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));

const truncatedMessage = (overrides: Partial<Message> = {}): Message => ({
  id: "m-trunc",
  role: "assistant",
  text: "I stopped after reaching the 12-iteration limit.",
  streaming: false,
  status: "completed",
  created_at: Date.now(),
  metadata: {
    thinking_count: 12,
    tokens_in: 1000,
    tokens_out: 200,
    truncated: true,
    compactions: 2,
  },
  ...overrides,
});

describe("MessageItem truncated continue affordance", () => {
  const continueRunSpy = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    continueRunSpy.mockClear();
    useChat.setState({
      messages: [],
      status: "idle",
      continueRun: continueRunSpy,
    });
  });

  it("renders the continue row for a truncated assistant message", () => {
    render(<MessageItem message={truncatedMessage()} />);
    expect(screen.getByTestId("message-truncated-m-trunc")).toBeInTheDocument();
    expect(screen.getByTestId("message-continue-m-trunc")).toBeInTheDocument();
    expect(screen.getByText("迭代预算已用尽")).toBeInTheDocument();
    // compactions count surfaces alongside the badge
    expect(screen.getByText("上下文已压缩 2 次")).toBeInTheDocument();
  });

  it("hides the row when the message is not truncated", () => {
    render(
      <MessageItem
        message={truncatedMessage({
          metadata: { thinking_count: 3, tokens_in: 10, tokens_out: 5 },
        })}
      />,
    );
    expect(screen.queryByTestId("message-truncated-m-trunc")).not.toBeInTheDocument();
  });

  it("hides the row while the message is still streaming", () => {
    render(<MessageItem message={truncatedMessage({ streaming: true })} />);
    expect(screen.queryByTestId("message-truncated-m-trunc")).not.toBeInTheDocument();
  });

  it("hides the row on user messages even with truncated metadata", () => {
    render(<MessageItem message={truncatedMessage({ role: "user" })} />);
    expect(screen.queryByTestId("message-truncated-m-trunc")).not.toBeInTheDocument();
  });

  it("clicking continue delegates to the chat store action", () => {
    render(<MessageItem message={truncatedMessage()} />);
    fireEvent.click(screen.getByTestId("message-continue-m-trunc"));
    expect(continueRunSpy).toHaveBeenCalledTimes(1);
  });

  it("disables the button while a run is active", () => {
    useChat.setState({ status: "streaming" });
    render(<MessageItem message={truncatedMessage()} />);
    expect(screen.getByTestId("message-continue-m-trunc")).toBeDisabled();
  });
});
