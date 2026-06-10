/**
 * Tests for the MessageInput — verify keyboard submit, the disabled
 * state during streaming, and the custom `minimax:suggestion` event.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageInput } from "../src/components/MessageInput";
import { useChat } from "../src/stores";

describe("MessageInput", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  });

  it("renders a textarea and a disabled send button", () => {
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    const send = screen.getByTestId("message-input-send");
    expect(textarea).toBeInTheDocument();
    expect(send).toBeDisabled();
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
    expect(screen.getByTestId("message-input-stopping")).toHaveTextContent("正在停止...");
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
});
