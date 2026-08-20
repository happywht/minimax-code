/**
 * Tests for the MessageInput — verify keyboard submit, the disabled
 * state during streaming, and the custom `minimax:suggestion` event.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageInput } from "../src/components/chat/MessageInput";
import { useChat, useCodebaseStore } from "../src/stores";
import type { AgentInfo } from "../src/types/ipc";

const AGENTS: AgentInfo[] = [
  {
    id: "general",
    name: "General",
    description: "default helper",
    enabled: true,
  },
  {
    id: "code-reviewer",
    name: "Code Reviewer",
    description: "reviews diffs",
    enabled: true,
  },
];

describe("MessageInput", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
    useCodebaseStore.getState().reset();
  });

  it("renders a textarea and a disabled send button", () => {
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    const send = screen.getByTestId("message-input-send");
    expect(textarea).toBeInTheDocument();
    expect(send).toBeDisabled();
    expect(screen.queryByTestId("message-input-token-warning")).toBeNull();
    expect(screen.getByTestId("message-input-token-count")).toHaveAccessibleName(
      "输入字符 0/8000",
    );
  });

  it("enables the send button when text is typed", async () => {
    const user = userEvent.setup();
    render(<MessageInput />);
    await user.type(screen.getByTestId("message-input-textarea"), "hi");
    expect(screen.getByTestId("message-input-send")).not.toBeDisabled();
  });

  it("submits on Enter and calls chat.send", async () => {
    const spy = vi.spyOn(useChat.getState(), "send");
    const user = userEvent.setup();
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    await user.type(textarea, "ping{enter}");
    expect(spy).toHaveBeenCalledWith("ping");
    spy.mockRestore();
  });

  it("does not submit on Shift+Enter (allows newlines)", () => {
    const spy = vi.spyOn(useChat.getState(), "send");
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    textarea.focus();
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("shows the cancel button when status is streaming", () => {
    useChat.setState({ status: "streaming" });
    render(<MessageInput />);
    expect(screen.getByTestId("message-input-cancel")).toBeInTheDocument();
  });

  it("shows a stopping transition when status is cancelling", () => {
    useChat.setState({ status: "cancelling" });
    render(<MessageInput />);
    expect(screen.getByTestId("message-input-cancel")).toBeDisabled();
    expect(screen.getByTestId("message-input-stopping")).toHaveTextContent("正在停止…");
  });

  it("warns before the input reaches the hard limit and blocks over-limit sends", () => {
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");

    fireEvent.change(textarea, { target: { value: "a".repeat(6400) } });
    expect(screen.getByTestId("message-input-token-warning")).toHaveTextContent("接近上限");
    expect(screen.getByTestId("message-input-token-meter").firstElementChild).toHaveStyle({
      width: "80%",
    });
    expect(screen.getByTestId("message-input-send")).not.toBeDisabled();

    fireEvent.change(textarea, { target: { value: "a".repeat(8001) } });
    expect(screen.getByTestId("message-input-token-warning")).toHaveTextContent("已超限");
    expect(screen.getByTestId("message-input-send")).toBeDisabled();
  });

  it("opens the unified mention picker for @agent queries", async () => {
    const user = userEvent.setup();
    render(<MessageInput loadAgents={async () => AGENTS} />);
    await user.type(screen.getByTestId("message-input-textarea"), "@code");

    expect(await screen.findByTestId("message-input-mention-picker")).toBeInTheDocument();
    expect(
      screen.getByTestId("message-input-mention-picker-item-agent-code-reviewer"),
    ).toHaveTextContent("Code Reviewer");
    expect(screen.queryByTestId("message-input-mention-picker-item-agent-general")).toBeNull();
  });

  it("opens the repo mention picker for @repo", async () => {
    const user = userEvent.setup();
    render(<MessageInput loadAgents={async () => AGENTS} />);
    await user.type(screen.getByTestId("message-input-textarea"), "@repo");
    expect(await screen.findByTestId("message-input-mention-picker")).toBeInTheDocument();
    expect(
      screen.getByTestId("message-input-mention-picker-item-repo-repo"),
    ).toHaveTextContent("Current repository");
  });

  it("opens the file mention picker for #file using recent files", async () => {
    useCodebaseStore.setState({ recentFiles: ["src/auth.ts", "src/api.ts"] });
    const user = userEvent.setup();
    render(<MessageInput loadAgents={async () => AGENTS} />);
    await user.type(screen.getByTestId("message-input-textarea"), "#api");
    expect(await screen.findByTestId("message-input-mention-picker")).toBeInTheDocument();
    expect(
      screen.getByTestId("message-input-mention-picker-item-file-src/api.ts"),
    ).toHaveTextContent("api.ts");
  });
});
