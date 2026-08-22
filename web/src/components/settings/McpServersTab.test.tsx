import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { McpServer } from "../../types/ipc";

vi.mock("../../ipc", () => ({
  typedIPC: {
    listMcpServers: vi.fn(),
    addMcpServer: vi.fn(),
    updateMcpServer: vi.fn(),
    removeMcpServer: vi.fn(),
    listMcpTools: vi.fn(),
  },
}));

import { typedIPC } from "../../ipc";
import { McpServersTab } from "./McpServersTab";

const listMcpServers = vi.mocked(typedIPC.listMcpServers);
const addMcpServer = vi.mocked(typedIPC.addMcpServer);
const updateMcpServer = vi.mocked(typedIPC.updateMcpServer);
const listMcpTools = vi.mocked(typedIPC.listMcpTools);

function makeServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: "srv",
    name: "Server",
    transport: "stdio",
    command: ["echo"],
    url: null,
    env: null,
    enabled: true,
    connected: false,
    bearer_token: null,
    headers: null,
    oauth_client_id: null,
    oauth_client_secret: null,
    oauth_scopes: null,
    oauth_callback_port: null,
    tool_states: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

async function waitForLoading() {
  await waitFor(() => {
    expect(screen.queryByText(/Loading MCP servers/i)).not.toBeInTheDocument();
  });
}

describe("McpServersTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMcpServers.mockResolvedValue({ servers: [] });
    listMcpTools.mockResolvedValue({
      server_name: "Server",
      tools: [
        { name: "read_file", description: "Read a file", inputSchema: { type: "object" } },
      ],
    });
  });

  it("renders empty state when no servers are configured", async () => {
    render(<McpServersTab />);
    await waitForLoading();
    expect(screen.getByText(/暂无 MCP Server/i)).toBeInTheDocument();
    expect(listMcpServers).toHaveBeenCalled();
  });

  it("adds a stdio MCP server and lists it", async () => {
    const user = userEvent.setup();
    addMcpServer.mockResolvedValue({ server: makeServer({ id: "fs", name: "Filesystem" }) });
    listMcpServers.mockResolvedValue({ servers: [makeServer({ id: "fs", name: "Filesystem" })] });

    render(<McpServersTab />);
    await waitForLoading();

    await user.click(screen.getByTestId("settings-mcp-add"));
    await user.type(screen.getByTestId("settings-mcp-name"), "Filesystem");
    await user.type(
      screen.getByTestId("settings-mcp-command"),
      "npx @modelcontextprotocol/server-filesystem .",
    );
    fireEvent.change(screen.getByTestId("settings-mcp-env"), {
      target: { value: '{"NODE_PATH":"/usr/local"}' },
    });
    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(addMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "filesystem",
          name: "Filesystem",
          transport: "stdio",
          command: ["npx", "@modelcontextprotocol/server-filesystem", "."],
          env: { NODE_PATH: "/usr/local" },
        }),
      );
    });
  });

  it("adds an SSE MCP server with OAuth fields", async () => {
    const user = userEvent.setup();
    addMcpServer.mockResolvedValue({ server: makeServer({ id: "remote", name: "Remote", transport: "sse" }) });
    listMcpServers.mockResolvedValue({ servers: [makeServer({ id: "remote", name: "Remote", transport: "sse" })] });

    render(<McpServersTab />);
    await waitForLoading();

    await user.click(screen.getByTestId("settings-mcp-add"));
    await user.type(screen.getByTestId("settings-mcp-name"), "Remote");
    await user.selectOptions(screen.getByTestId("settings-mcp-transport"), "sse");
    await user.type(screen.getByTestId("settings-mcp-url"), "http://localhost:3001/sse");
    await user.type(screen.getByTestId("settings-mcp-bearer"), "secret");
    fireEvent.change(screen.getByTestId("settings-mcp-headers"), {
      target: { value: '{"X-Custom":"yes"}' },
    });
    await user.type(screen.getByTestId("settings-mcp-oauth-id"), "client");
    await user.type(screen.getByTestId("settings-mcp-oauth-secret"), "cs");
    await user.type(screen.getByTestId("settings-mcp-oauth-scopes"), "read,write");
    await user.type(screen.getByTestId("settings-mcp-oauth-port"), "8765");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(addMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "remote",
          name: "Remote",
          transport: "sse",
          url: "http://localhost:3001/sse",
          bearer_token: "secret",
          headers: { "X-Custom": "yes" },
          oauth_client_id: "client",
          oauth_client_secret: "cs",
          oauth_scopes: ["read", "write"],
          oauth_callback_port: 8765,
        }),
      );
    });
  });

  it("lists tools and toggles per-tool enablement", async () => {
    const user = userEvent.setup();
    listMcpServers.mockResolvedValue({ servers: [makeServer({ id: "tools", name: "Tools" })] });
    updateMcpServer.mockResolvedValue({ server: makeServer({ id: "tools", name: "Tools", tool_states: { read_file: false } }) });

    render(<McpServersTab />);
    await waitForLoading();

    await user.click(screen.getByTestId("settings-mcp-expand-tools"));
    await waitFor(() => {
      expect(screen.getByText("read_file")).toBeInTheDocument();
    });

    const checkbox = screen.getByTestId("settings-mcp-tool-tools-read_file");
    expect(checkbox).toBeChecked();
    await user.click(checkbox);
    await waitFor(() => {
      expect(updateMcpServer).toHaveBeenCalledWith("tools", { tool_states: { read_file: false } });
    });
  });

  it("edits an existing server: prefills, submits via updateMcpServer, cancels", async () => {
    const user = userEvent.setup();
    listMcpServers.mockResolvedValue({
      servers: [
        makeServer({
          id: "fs",
          name: "Filesystem",
          command: ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"],
          env: { NODE_PATH: "/usr/local" },
          oauth_scopes: ["read", "write"],
          oauth_callback_port: 8765,
        }),
      ],
    });
    updateMcpServer.mockResolvedValue({
      server: makeServer({ id: "fs", name: "Filesystem", command: ["echo"] }),
    });
    listMcpTools.mockResolvedValue({ server_name: "Filesystem", tools: [] });

    render(<McpServersTab />);
    await waitForLoading();

    // Open the inline edit form — inputs are prefilled from the server row.
    await user.click(screen.getByTestId("settings-mcp-edit-fs"));
    expect(screen.getByTestId("settings-mcp-edit-form-fs")).toBeInTheDocument();
    expect(screen.getByTestId("settings-mcp-edit-name")).toHaveValue("Filesystem");
    expect(screen.getByTestId("settings-mcp-edit-command")).toHaveValue(
      "npx @modelcontextprotocol/server-filesystem /tmp",
    );
    expect(screen.getByTestId("settings-mcp-edit-env")).toHaveValue('{"NODE_PATH":"/usr/local"}');
    expect(screen.getByTestId("settings-mcp-edit-oauth-scopes")).toHaveValue("read, write");
    expect(screen.getByTestId("settings-mcp-edit-oauth-port")).toHaveValue(8765);

    // Change the command and save.
    await user.clear(screen.getByTestId("settings-mcp-edit-command"));
    await user.type(screen.getByTestId("settings-mcp-edit-command"), "node server.js");
    await user.click(screen.getByRole("button", { name: /编辑配置/ }));

    await waitFor(() => {
      expect(updateMcpServer).toHaveBeenCalledWith(
        "fs",
        expect.objectContaining({
          name: "Filesystem",
          transport: "stdio",
          command: ["node", "server.js"],
          env: { NODE_PATH: "/usr/local" },
          oauth_scopes: ["read", "write"],
          oauth_callback_port: 8765,
        }),
      );
    });
    // The inline form closes after a successful save.
    await waitFor(() => {
      expect(screen.queryByTestId("settings-mcp-edit-form-fs")).not.toBeInTheDocument();
    });
  });

  it("edit cancel closes the form without calling updateMcpServer", async () => {
    const user = userEvent.setup();
    listMcpServers.mockResolvedValue({ servers: [makeServer({ id: "fs", name: "Filesystem" })] });

    render(<McpServersTab />);
    await waitForLoading();

    await user.click(screen.getByTestId("settings-mcp-edit-fs"));
    await user.click(screen.getByTestId("settings-mcp-edit-cancel"));

    expect(screen.queryByTestId("settings-mcp-edit-form-fs")).not.toBeInTheDocument();
    expect(updateMcpServer).not.toHaveBeenCalled();
  });
});
