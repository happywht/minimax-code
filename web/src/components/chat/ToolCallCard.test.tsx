import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ToolCallCard } from "./ToolCallCard";
import type { Message } from "../../types/ipc";

function makeMessage(fields: Partial<Message>): Message {
  return {
    id: "msg-1",
    role: "tool",
    text: "result",
    streaming: false,
    created_at: Date.now(),
    ...fields,
  };
}

describe("ToolCallCard", () => {
  it("renders a plain tool name when the tool is not from an MCP server", () => {
    render(<ToolCallCard message={makeMessage({ tool_name: "read_file" })} />);
    expect(screen.getByText("read_file")).toBeInTheDocument();
  });

  it("renders the original tool name and MCP server badge for bridged tools", () => {
    render(<ToolCallCard message={makeMessage({ tool_name: "mcp__filesystem__read_file" })} />);
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("filesystem")).toBeInTheDocument();
  });

  it("handles names with underscores in the server part", () => {
    render(
      <ToolCallCard message={makeMessage({ tool_name: "mcp__git_server__log" })} />,
    );
    expect(screen.getByText("log")).toBeInTheDocument();
    expect(screen.getByText("git_server")).toBeInTheDocument();
  });
});
