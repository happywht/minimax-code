/**
 * Tests for the MCP tool tester (P1-5) — expanding a tool row opens an
 * inline runner over `mcp.invoke_tool` with JSON args editing, arg
 * validation, and text / structured result rendering.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// vi.mock is hoisted — create the mock fns via vi.hoisted so the
// factory can reference them safely.
const { listMcpServers, listMcpTools, invokeMcpTool } = vi.hoisted(() => ({
  listMcpServers: vi.fn(),
  listMcpTools: vi.fn(),
  invokeMcpTool: vi.fn(),
}));

vi.mock("../src/ipc", () => ({
  typedIPC: {
    listMcpServers,
    listMcpTools,
    invokeMcpTool,
  },
}));
vi.mock("../src/components/modals/ConfirmationDialog", () => ({
  requestConfirmation: vi.fn(),
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { McpServersTab } from "../src/components/settings/McpServersTab";

const SERVER = {
  id: "srv_1",
  name: "fs",
  transport: "stdio" as const,
  command: ["npx", "server-fs"],
  url: null,
  env: null,
  enabled: true,
  connected: true,
  bearer_token: null,
  headers: null,
  oauth_client_id: null,
  oauth_client_secret: null,
  oauth_scopes: null,
  oauth_callback_port: null,
  tool_states: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const TOOLS = [
  {
    name: "read_file",
    description: "读文件",
    inputSchema: { properties: { path: { type: "string" } } },
  },
  { name: "list_dir", description: "列目录", inputSchema: {} },
];

/** Render the tab, expand the server, open the tester for `read_file`. */
async function openTester() {
  render(<McpServersTab />);
  await waitFor(() => {
    expect(screen.getByTestId("settings-mcp-server-srv_1")).toBeInTheDocument();
  });
  fireEvent.click(screen.getByTestId("settings-mcp-expand-srv_1"));
  await waitFor(() => {
    expect(screen.getByTestId("settings-mcp-tools-srv_1")).toBeInTheDocument();
  });
  fireEvent.click(screen.getByTestId("settings-mcp-test-srv_1-read_file"));
  await waitFor(() => {
    expect(
      screen.getByTestId("settings-mcp-tester-srv_1-read_file"),
    ).toBeInTheDocument();
  });
}

describe("MCP tool tester (P1-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMcpServers.mockResolvedValue({ servers: [SERVER] });
    listMcpTools.mockResolvedValue({ server_name: "fs", tools: TOOLS });
    invokeMcpTool.mockResolvedValue({
      ok: true,
      server_name: "fs",
      tool_name: "read_file",
      text: null,
      content: null,
      isError: false,
    });
  });

  it("opens an inline tester with the schema hint when a tool's test button is clicked", async () => {
    await openTester();

    expect(
      screen.getByTestId("settings-mcp-tester-args-srv_1-read_file"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("settings-mcp-tester-run-srv_1-read_file")).toBeInTheDocument();
    // inputSchema keys surface as a hint so the user knows what to fill.
    expect(screen.getByText("参数字段：path")).toBeInTheDocument();
  });

  it("rejects non-object JSON args without invoking the tool", async () => {
    await openTester();

    fireEvent.change(
      screen.getByTestId("settings-mcp-tester-args-srv_1-read_file"),
      { target: { value: "[1,2]" } },
    );
    fireEvent.click(screen.getByTestId("settings-mcp-tester-run-srv_1-read_file"));

    await waitFor(() => {
      expect(
        screen.getByTestId("settings-mcp-tester-error-srv_1-read_file"),
      ).toHaveTextContent("参数必须是 JSON 对象");
    });
    expect(invokeMcpTool).not.toHaveBeenCalled();
  });

  it("invokes the tool with the parsed args and renders the text result", async () => {
    invokeMcpTool.mockResolvedValue({
      ok: true,
      server_name: "fs",
      tool_name: "read_file",
      text: "hello world",
      content: null,
      isError: false,
    });
    await openTester();

    fireEvent.change(
      screen.getByTestId("settings-mcp-tester-args-srv_1-read_file"),
      { target: { value: '{"path":"src/main.py"}' } },
    );
    fireEvent.click(screen.getByTestId("settings-mcp-tester-run-srv_1-read_file"));

    await waitFor(() => {
      expect(
        screen.getByTestId("settings-mcp-tester-result-srv_1-read_file"),
      ).toBeInTheDocument();
    });
    expect(invokeMcpTool).toHaveBeenCalledWith("fs", "read_file", {
      path: "src/main.py",
    });
    expect(
      screen.getByTestId("settings-mcp-tester-result-srv_1-read_file"),
    ).toHaveTextContent("hello world");
  });

  it("renders isError results with the error styling", async () => {
    invokeMcpTool.mockResolvedValue({
      ok: true,
      server_name: "fs",
      tool_name: "read_file",
      text: "file not found",
      content: null,
      isError: true,
    });
    await openTester();

    fireEvent.click(screen.getByTestId("settings-mcp-tester-run-srv_1-read_file"));

    const result = await screen.findByTestId(
      "settings-mcp-tester-result-srv_1-read_file",
    );
    expect(result).toHaveTextContent("工具返回错误");
    expect(result).toHaveTextContent("file not found");
  });

  it("shows the invoke failure inline when the RPC rejects", async () => {
    invokeMcpTool.mockRejectedValue(new Error("server offline"));
    await openTester();

    fireEvent.click(screen.getByTestId("settings-mcp-tester-run-srv_1-read_file"));

    await waitFor(() => {
      expect(
        screen.getByTestId("settings-mcp-tester-error-srv_1-read_file"),
      ).toHaveTextContent("试运行失败");
    });
  });

  it("collapses the tester when the test button is clicked again", async () => {
    await openTester();

    fireEvent.click(screen.getByTestId("settings-mcp-test-srv_1-read_file"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("settings-mcp-tester-srv_1-read_file"),
      ).not.toBeInTheDocument();
    });
  });
});
