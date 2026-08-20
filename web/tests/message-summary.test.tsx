/**
 * Tests for the per-turn summary row in MessageItem.
 *
 * The summary line "思考 N 次 · 查看 M 个文件 · 修改 K 个文件" appears
 * at the top of completed assistant bubbles. Counts are derived from the
 * tool-call/tool-result messages that follow the assistant message in
 * the same turn (bounded by the next user/assistant message).
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageItem } from "../src/components/chat/MessageItem";
import { useChat } from "../src/stores";
import type { Message } from "../src/types/ipc";

const ts = (offset = 0): number => 1_700_000_000_000 + offset;

const baseMessage = (overrides: Partial<Message> = {}): Message => ({
  id: "m1",
  role: "assistant",
  text: "hello",
  streaming: false,
  created_at: ts(0),
  ...overrides,
});

function setMessages(messages: Message[]): void {
  useChat.setState({
    messages,
    status: "idle",
    error: null,
    agentReady: false,
  });
}

describe("MessageItem per-turn summary", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  });

  it("renders the summary row on assistant bubbles with the right testId", () => {
    setMessages([baseMessage({ id: "a1", text: "ok" })]);
    render(<MessageItem message={baseMessage({ id: "a1", text: "ok" })} />);
    expect(screen.getByTestId("message-summary-a1")).toBeInTheDocument();
  });

  it("does not render the summary row on user bubbles", () => {
    const userMsg: Message = {
      id: "u1",
      role: "user",
      text: "hi",
      streaming: false,
      created_at: ts(0),
    };
    setMessages([userMsg]);
    render(<MessageItem message={userMsg} />);
    expect(screen.queryByTestId("message-summary-u1")).toBeNull();
  });

  it("does not show zero-value final statistics while a reply is streaming", () => {
    const streaming = baseMessage({
      id: "a-streaming",
      streaming: true,
      status: "streaming",
    });
    setMessages([streaming]);
    render(<MessageItem message={streaming} />);
    expect(screen.queryByTestId("message-summary-a-streaming")).toBeNull();
    expect(screen.getByTestId("message-status-a-streaming")).toHaveTextContent(
      "生成中",
    );
  });

  it("shows all-zero counts when the turn has no tool calls", () => {
    setMessages([baseMessage({ id: "a1" })]);
    render(<MessageItem message={baseMessage({ id: "a1" })} />);
    const summary = screen.getByTestId("message-summary-a1");
    expect(summary).toHaveTextContent("思考 0 次");
    expect(summary).toHaveTextContent("查看 0 个文件");
    expect(summary).toHaveTextContent("修改 0 个文件");
  });

  it("counts read_file / list_files tool messages as 'viewed'", () => {
    setMessages([
      baseMessage({ id: "a1" }),
      {
        id: "tc-1",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(1),
        tool_name: "read_file",
      },
      {
        id: "tc-2",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(2),
        tool_name: "list_files",
      },
      {
        id: "tc-3",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(3),
        tool_name: "search_files",
      },
    ]);
    render(<MessageItem message={baseMessage({ id: "a1" })} />);
    const summary = screen.getByTestId("message-summary-a1");
    expect(summary).toHaveTextContent("查看 3 个文件");
    expect(summary).toHaveTextContent("修改 0 个文件");
  });

  it("counts write_file / edit_file tool messages as 'modified'", () => {
    setMessages([
      baseMessage({ id: "a1" }),
      {
        id: "tc-1",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(1),
        tool_name: "write_file",
      },
      {
        id: "tc-2",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(2),
        tool_name: "edit_file",
      },
      {
        id: "tc-3",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(3),
        tool_name: "create_file",
      },
    ]);
    render(<MessageItem message={baseMessage({ id: "a1" })} />);
    expect(screen.getByTestId("message-summary-a1")).toHaveTextContent(
      "修改 3 个文件",
    );
  });

  it("stops counting at the next user or assistant message", () => {
    setMessages([
      baseMessage({ id: "a1" }),
      {
        id: "tc-1",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(1),
        tool_name: "read_file",
      },
      // next user message — closes the turn
      {
        id: "u2",
        role: "user",
        text: "next turn",
        streaming: false,
        created_at: ts(2),
      },
      {
        id: "a2",
        role: "assistant",
        text: "answer",
        streaming: false,
        created_at: ts(3),
      },
      // tool call after a2 — must NOT count toward a1
      {
        id: "tc-2",
        role: "tool",
        text: "...",
        streaming: false,
        created_at: ts(4),
        tool_name: "write_file",
      },
    ]);
    render(<MessageItem message={baseMessage({ id: "a1" })} />);
    const summary = screen.getByTestId("message-summary-a1");
    expect(summary).toHaveTextContent("查看 1 个文件");
    expect(summary).toHaveTextContent("修改 0 个文件");
  });

  it("reads thinking_count from message.metadata when present", () => {
    const self = baseMessage({ id: "a1" });
    self.metadata = { thinking_count: 3, tokens_in: 12, tokens_out: 3 };
    setMessages([self]);
    render(<MessageItem message={self} />);
    expect(screen.getByTestId("message-summary-a1")).toHaveTextContent(
      "思考 3 次",
    );
  });
});
