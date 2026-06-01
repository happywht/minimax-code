/**
 * Tests for the MessageItem — covers all four roles (user,
 * assistant, tool, system), markdown rendering, and the streaming
 * cursor.
 */
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageItem } from "../src/components/MessageItem";
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
    expect(el.querySelector("div")?.className).toContain("text-red-300");
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
});
