/**
 * Tests for @repo / #file context resolution in MessageInput.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageInput } from "../src/components/chat/MessageInput";
import { useChat, useCodebaseStore } from "../src/stores";

vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      summarizeCodebasePath: vi.fn(async (path: string) => ({
        path,
        kind: "file" as const,
        language: "ts",
        total_lines: 120,
        symbols: [],
        snippet: `Summary of ${path}`,
        file_count: 1,
      })),
      searchCodebase: vi.fn(async (query: string) => ({
        query,
        file_pattern: null,
        total: 1,
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
        ],
      })),
    },
  };
});

describe("MessageInput mention context", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
    useCodebaseStore.getState().reset();
  });

  it("prepends file summary when sending a #file mention", async () => {
    const spy = vi.spyOn(useChat.getState(), "send");
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    fireEvent.change(textarea, { target: { value: "explain #src/auth.ts" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });
    const sent = spy.mock.calls[0][0] as string;
    expect(sent).toContain("<file path=\"src/auth.ts\">");
    expect(sent).toContain("Summary of src/auth.ts");
    expect(sent).toContain("explain");
    spy.mockRestore();
  });

  it("prepends repo search results when sending @repo", async () => {
    const { typedIPC } = await import("../src/ipc");
    const spy = vi.spyOn(useChat.getState(), "send");
    render(<MessageInput />);
    const textarea = screen.getByTestId("message-input-textarea");
    fireEvent.change(textarea, { target: { value: "@repo auth flow" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => {
      expect(typedIPC.searchCodebase).toHaveBeenCalledWith("auth flow", { limit: 6 });
    });
    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });
    const sent = spy.mock.calls[0][0] as string;
    expect(sent).toContain("<search query=\"auth flow\">");
    expect(sent).toContain("src/auth.ts L1-10");
    spy.mockRestore();
  });
});
