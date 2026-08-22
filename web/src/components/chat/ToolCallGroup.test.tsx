/**
 * Tests for ToolCallGroup — the collapsible summary row that wraps runs
 * of consecutive tool-call cards (keeps long agent turns scannable).
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ToolCallGroup } from "./ToolCallGroup";
import type { Message } from "../../types/ipc";

function makeMessage(fields: Partial<Message>): Message {
  return {
    id: "msg-1",
    role: "tool",
    text: "result",
    streaming: false,
    created_at: 1,
    ...fields,
  };
}

function makeGroup(n: number, streaming = false): Message[] {
  return Array.from({ length: n }, (_, i) =>
    makeMessage({
      id: `t${i + 1}`,
      tool_name: i % 2 === 0 ? "read_file" : "exec_command",
      streaming: streaming && i === n - 1,
    }),
  );
}

describe("ToolCallGroup", () => {
  it("collapses by default: one summary line, no individual cards", () => {
    render(<ToolCallGroup messages={makeGroup(4)} />);
    const group = screen.getByTestId("message-tool-group");
    expect(group).toHaveAttribute("data-role", "tool-group");
    expect(screen.getByText("工具调用 × 4")).toBeInTheDocument();
    // Individual tool cards are hidden until expanded.
    expect(screen.queryAllByTestId("message-tool")).toHaveLength(0);
  });

  it("summarizes distinct tool names with per-tool counts", () => {
    render(<ToolCallGroup messages={makeGroup(4)} />);
    expect(screen.getByText(/read_file ×2/)).toBeInTheDocument();
    expect(screen.getByText(/exec_command ×2/)).toBeInTheDocument();
  });

  it("expands on click and reveals the individual tool cards", () => {
    render(<ToolCallGroup messages={makeGroup(3)} />);
    const toggle = screen.getByRole("button", { name: /工具调用 × 3/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("message-tool")).toHaveLength(3);
    // Clicking again collapses back.
    fireEvent.click(toggle);
    expect(screen.queryAllByTestId("message-tool")).toHaveLength(0);
  });

  it("starts expanded while a tool call is still streaming (live progress stays visible)", () => {
    render(<ToolCallGroup messages={makeGroup(3, true)} />);
    const toggle = screen.getByRole("button", { name: /工具调用 × 3/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("message-tool")).toHaveLength(3);
  });

  it("respects an explicit click over the streaming default", () => {
    render(<ToolCallGroup messages={makeGroup(3, true)} />);
    const toggle = screen.getByRole("button", { name: /工具调用 × 3/ });
    fireEvent.click(toggle); // user collapses the live group
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryAllByTestId("message-tool")).toHaveLength(0);
  });
});
