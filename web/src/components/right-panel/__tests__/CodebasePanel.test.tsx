/**
 * Tests for CodebasePanel — status polling, search and summarize wiring.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CodebasePanel } from "../CodebasePanel";
import { useCodebaseStore } from "../../../stores";

vi.mock("../../../components/layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

const mockStatus = {
  status: "done" as const,
  processed: 12,
  total: 12,
  percent: 100,
  message: "Index up to date",
  error: null,
  stats: { total_chunks: 12, total_files: 12, latest_updated_at: new Date().toISOString() },
};

vi.mock("../../../ipc", async () => {
  const actual = await vi.importActual<typeof import("../../../ipc")>("../../../ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      getCodebaseStatus: vi.fn(async () => mockStatus),
      buildCodebaseIndex: vi.fn(async () => mockStatus),
      searchCodebase: vi.fn(async () => ({
        query: "auth",
        file_pattern: null,
        total: 2,
        results: [
          {
            chunk_id: "c1",
            file_path: "src/auth.ts",
            start_line: 1,
            end_line: 10,
            snippet: "export function login() {}",
            language: "ts",
            rank: 1,
            symbols: [],
          },
          {
            chunk_id: "c2",
            file_path: "src/auth.ts",
            start_line: 11,
            end_line: 20,
            snippet: "export function logout() {}",
            language: "ts",
            rank: 2,
            symbols: [],
          },
        ],
      })),
      summarizeCodebasePath: vi.fn(async () => ({
        path: "src/auth.ts",
        kind: "file" as const,
        language: "ts",
        total_lines: 120,
        symbols: [],
        snippet: "Auth utilities",
        file_count: 1,
      })),
    },
  };
});

describe("CodebasePanel", () => {
  beforeEach(() => {
    useCodebaseStore.getState().reset();
  });

  it("loads and displays index status on mount", async () => {
    const { typedIPC } = await import("../../../ipc");
    render(<CodebasePanel />);
    await waitFor(() => {
      expect(typedIPC.getCodebaseStatus).toHaveBeenCalled();
    });
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("文件：12")).toBeInTheDocument();
  });

  it("runs a search and renders results", async () => {
    const { typedIPC } = await import("../../../ipc");
    render(<CodebasePanel />);
    const input = screen.getByTestId("codebase-search");
    fireEvent.change(input, { target: { value: "auth" } });
    fireEvent.click(screen.getByTestId("codebase-search-btn"));

    await waitFor(() => {
      expect(typedIPC.searchCodebase).toHaveBeenCalledWith("auth");
    });
    expect(screen.getByTestId("codebase-results")).toBeInTheDocument();
    expect(screen.getAllByTestId("codebase-result")).toHaveLength(2);
    expect(screen.getAllByText("src/auth.ts").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("热门文件")).toBeInTheDocument();
  });

  it("summarizes a path and renders the summary", async () => {
    const { typedIPC } = await import("../../../ipc");
    render(<CodebasePanel />);
    const input = screen.getByTestId("codebase-summary-path");
    fireEvent.change(input, { target: { value: "src/auth.ts" } });
    fireEvent.click(screen.getByTestId("codebase-summary-btn"));

    await waitFor(() => {
      expect(typedIPC.summarizeCodebasePath).toHaveBeenCalledWith("src/auth.ts");
    });
    expect(screen.getByText("Auth utilities")).toBeInTheDocument();
    expect(screen.getByText("最近查看")).toBeInTheDocument();
  });
});
