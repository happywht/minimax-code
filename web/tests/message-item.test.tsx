/**
 * Tests for the MessageItem — covers all four roles (user,
 * assistant, tool, system), markdown rendering, and the streaming
 * cursor.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageItem } from "../src/components/MessageItem";
import { useChat } from "../src/stores";
import type { Message } from "../src/types/ipc";

const baseMessage = (overrides: Partial<Message> = {}): Message => ({
  id: "m1",
  role: "user",
  text: "hi",
  streaming: false,
  created_at: Date.now(),
  ...overrides,
});

describe("MessageItem", () => {
  it("renders a user bubble with the right-aligned style", () => {
    render(<MessageItem message={baseMessage({ role: "user", text: "hi there" })} />);
    const el = screen.getByTestId("message-user");
    expect(el).toHaveAttribute("data-role", "user");
    expect(screen.getByText("hi there")).toBeInTheDocument();
  });

  it("renders an assistant message with markdown", async () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "Here is `code` and **bold**.",
        })}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("code")).toBeInTheDocument();
    });
    expect(screen.getByText("bold")).toBeInTheDocument();
  });

  it("renders a tool bubble that expands on click", () => {
    const tool: Message = baseMessage({
      id: "t1",
      role: "tool",
      text: "first line\nsecond line",
      tool_name: "read_file",
      tool_args: { path: "x.py" },
    });
    render(<MessageItem message={tool} />);
    const trigger = screen.getByRole("button", { expanded: false });
    expect(trigger).toHaveTextContent(/read_file/);
    fireEvent.click(trigger);
    expect(screen.getByText(/first line/)).toBeInTheDocument();
  });

  it("renders a system message in the error style", () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "system",
          text: "Something broke",
        })}
      />,
    );
    const el = screen.getByTestId("message-system");
    expect(el).toHaveAttribute("data-role", "system");
    expect(el.querySelector("div")?.className).toContain("text-status-error");
  });

  it("shows the streaming cursor when streaming is true (assistant)", () => {
    render(
      <MessageItem
        message={baseMessage({
          role: "assistant",
          text: "partial…",
          streaming: true,
        })}
      />,
    );
    expect(screen.getByText("▍")).toBeInTheDocument();
  });

  it("renders a queued assistant message as a skeleton", () => {
    render(
      <MessageItem
        message={baseMessage({
          id: "a-waiting",
          role: "assistant",
          text: "",
          streaming: true,
          status: "queued",
        })}
      />,
    );
    expect(screen.getByTestId("message-status-a-waiting")).toHaveTextContent("Waiting");
    expect(screen.getByTestId("message-skeleton-a-waiting")).toBeInTheDocument();
  });

  it("renders a failed assistant message with retry", () => {
    const retrySpy = vi.spyOn(useChat.getState(), "retryMessage").mockResolvedValue(undefined);
    render(
      <MessageItem
        message={baseMessage({
          id: "a-failed",
          role: "assistant",
          text: "network down",
          streaming: false,
          status: "failed",
          error: "network down",
          retry_content: "try again",
        })}
      />,
    );
    expect(screen.getByTestId("message-status-a-failed")).toHaveTextContent("Failed");
    fireEvent.click(screen.getByTestId("message-retry-a-failed"));
    expect(retrySpy).toHaveBeenCalledWith("a-failed");
    retrySpy.mockRestore();
  });
});
