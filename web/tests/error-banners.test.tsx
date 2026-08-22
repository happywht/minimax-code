/**
 * Tests for the panel error banners (P1-7) — load failures surface as
 * persistent inline banners (retryable where it makes sense) instead of
 * one-shot toasts, so an empty list is never read as "no data".
 *
 * Stores/panels import ``typedIPC`` directly from ``ipc/client``, so the
 * module mock targets the client module itself.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { listCheckpoints, listPlugins, listMcpServers, listMemories, sessionStats } =
  vi.hoisted(() => ({
    listCheckpoints: vi.fn(),
    listPlugins: vi.fn(),
    listMcpServers: vi.fn(),
    listMemories: vi.fn(),
    sessionStats: vi.fn(),
  }));

vi.mock("../src/ipc/client", () => ({
  typedIPC: {
    listCheckpoints,
    listPlugins,
    listMcpServers,
    listMemories,
    sessionStats,
  },
  ipc: { on: vi.fn(() => vi.fn()) },
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { CheckpointPanel } from "../src/components/right-panel/CheckpointPanel";
import { PluginsTab } from "../src/components/settings/PluginsTab";
import { McpServersTab } from "../src/components/settings/McpServersTab";
import { MemoryTab } from "../src/components/settings/MemoryTab";
import { Sidebar } from "../src/components/layout/Sidebar";
import { useSessionStore } from "../src/stores/sessionStore";
import { useMemoryStore } from "../src/stores/memoryStore";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("panel load error banners (P1-7)", () => {
  it("CheckpointPanel shows a retryable banner when the list fails to load", async () => {
    listCheckpoints.mockRejectedValueOnce(new Error("db locked"));
    listCheckpoints.mockResolvedValueOnce({ checkpoints: [] });
    useSessionStore.setState({ currentSessionId: "ses_1" });

    render(<CheckpointPanel />);
    const banner = await screen.findByTestId("checkpoint-panel-error");
    expect(banner).toHaveTextContent("db locked");
    expect(screen.queryByText("暂无 Checkpoint")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("checkpoint-panel-error-retry"));
    await waitFor(() => {
      expect(screen.queryByTestId("checkpoint-panel-error")).not.toBeInTheDocument();
    });
    expect(listCheckpoints).toHaveBeenCalledTimes(2);
  });

  it("PluginsTab shows a retryable banner when the list fails to load", async () => {
    listPlugins.mockRejectedValueOnce(new Error("registry offline"));
    listPlugins.mockResolvedValueOnce({ plugins: [] });

    render(<PluginsTab />);
    const banner = await screen.findByTestId("settings-plugins-error");
    expect(banner).toHaveTextContent("registry offline");
    expect(screen.queryByText("暂无 Plugin")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("settings-plugins-error-retry"));
    await waitFor(() => {
      expect(screen.queryByTestId("settings-plugins-error")).not.toBeInTheDocument();
    });
    expect(listPlugins).toHaveBeenCalledTimes(2);
  });

  it("McpServersTab shows a retryable banner when the list fails to load", async () => {
    listMcpServers.mockRejectedValueOnce(new Error("transport down"));
    listMcpServers.mockResolvedValueOnce({ servers: [] });

    render(<McpServersTab />);
    const banner = await screen.findByTestId("settings-mcp-error");
    expect(banner).toHaveTextContent("transport down");
    expect(screen.queryByText("暂无 MCP Server")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("settings-mcp-error-retry"));
    await waitFor(() => {
      expect(screen.queryByTestId("settings-mcp-error")).not.toBeInTheDocument();
    });
    expect(listMcpServers).toHaveBeenCalledTimes(2);
  });

  it("MemoryTab shows a retryable banner when the list fails to load", async () => {
    listMemories.mockRejectedValueOnce(new Error("vector unavailable"));
    listMemories.mockResolvedValueOnce({ memories: [], total: 0 });
    useMemoryStore.setState({ memories: [], total: 0, error: null, searchQuery: "" });

    render(<MemoryTab />);
    const banner = await screen.findByTestId("settings-memory-error");
    expect(banner).toHaveTextContent("vector unavailable");

    fireEvent.click(screen.getByTestId("settings-memory-error-retry"));
    await waitFor(() => {
      expect(screen.queryByTestId("settings-memory-error")).not.toBeInTheDocument();
    });
    expect(listMemories).toHaveBeenCalledTimes(2);
  });

  it("Sidebar falls back to an inline stats row when sessionStats fails", async () => {
    sessionStats.mockRejectedValue(new Error("boom"));
    useSessionStore.setState({ sessions: [], projects: [], loading: false });

    render(<Sidebar onMobileClick={() => {}} />);
    expect(await screen.findByTestId("sidebar-stats-error")).toBeInTheDocument();
  });
});
