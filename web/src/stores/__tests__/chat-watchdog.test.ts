/**
 * Tests for the stall watchdog in the chat store.
 *
 * The watchdog fires a "Stream timed out" error after 60 seconds
 * of no activity. It must be reset by ALL streaming events:
 *   - agent.message_chunk (done=false)
 *   - agent.tool_call
 *   - agent.tool_result
 *   - agent.status
 *
 * Previously, only message_chunk reset the watchdog, causing
 * false timeouts during long tool execution (tool_timeout=120s
 * in the backend vs 60s watchdog in the frontend).
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { useChat } from "../chat";
import { StreamEvent } from "../../types/ipc";

// ── Capture event handlers registered via ipc.on ──────────────────
const handlers = new Map<string, (env: any) => void>();

function mockIpcOn(event: string, handler: (env: any) => void): () => void {
  handlers.set(event, handler);
  return () => handlers.delete(event);
}

// ── Mock IPC module ───────────────────────────────────────────────
const toastErrorSpy = vi.fn();

vi.mock("../../ipc", () => ({
  ipc: {
    start: vi.fn().mockResolvedValue(undefined),
    on: vi.fn((event: string, handler: (env: any) => void) => mockIpcOn(event, handler)),
    ping: vi.fn().mockResolvedValue(true),
  },
  IPCError: class extends Error {},
  typedIPC: {
    listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    createSession: vi.fn(),
    listMessages: vi.fn().mockResolvedValue({ messages: [] }),
    sendMessage: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    deleteSession: vi.fn(),
    updateSession: vi.fn(),
    cancelAgent: vi.fn(),
  },
}));

vi.mock("../../components/ErrorBoundary", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorSpy(...args),
  },
}));

vi.mock("../sessionStore", () => ({
  useSessionStore: {
    getState: vi.fn().mockReturnValue({ currentSessionId: "test-session" }),
    setState: vi.fn(),
  },
}));

describe("Stall watchdog", () => {
  // ── One-time setup: register all event handlers ─────────────────
  beforeAll(async () => {
    await useChat.getState().init();
  });

  beforeEach(() => {
    vi.useFakeTimers();
    toastErrorSpy.mockClear();
    // Reset store state but keep handlers registered
    useChat.setState({ messages: [], status: "idle", error: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Helper: emit an event ───────────────────────────────────────

  function emit(event: string, data: Record<string, unknown>): void {
    const handler = handlers.get(event);
    if (!handler) throw new Error(`No handler registered for event: ${event}`);
    handler({ data });
  }

  // ── Helpers: simulate streaming start ────────────────────────────

  function startStreaming(): void {
    // Put the store into "streaming" status (what send() does)
    useChat.setState({ status: "streaming" });
    // Emit a first chunk to kick off the watchdog
    emit(StreamEvent.MessageChunk, {
      message_id: "msg-1",
      delta: "Hello",
      done: false,
    });
  }

  // ── Tests ────────────────────────────────────────────────────────

  it("fires timeout error after 60 s of no activity", () => {
    startStreaming();
    expect(useChat.getState().status).toBe("streaming");

    // Advance past the 60s deadline
    vi.advanceTimersByTime(60_000);

    expect(useChat.getState().status).toBe("error");
    expect(useChat.getState().error).toContain("timed out");
    expect(toastErrorSpy).toHaveBeenCalledWith(
      "Stream timed out",
      expect.stringContaining("60 seconds"),
    );
  });

  it("does NOT fire timeout when tool_call arrives within 60 s", () => {
    startStreaming();

    // 50 seconds pass, then a tool_call event arrives
    vi.advanceTimersByTime(50_000);
    emit(StreamEvent.ToolCall, {
      tool_call_id: "tc-1",
      name: "read_file",
      args: { path: "/tmp/test.txt" },
    });

    // Another 50 seconds pass (100s total, but watchdog was reset)
    vi.advanceTimersByTime(50_000);

    expect(useChat.getState().status).toBe("streaming");
    expect(useChat.getState().error).toBeNull();
  });

  it("does NOT fire timeout when tool_result arrives within 60 s", () => {
    startStreaming();

    vi.advanceTimersByTime(50_000);
    emit(StreamEvent.ToolResult, {
      tool_call_id: "tc-1",
      result: "file contents here",
      error: null,
    });

    vi.advanceTimersByTime(50_000);

    expect(useChat.getState().status).toBe("streaming");
    expect(useChat.getState().error).toBeNull();
  });

  it("does NOT fire timeout when agent.status arrives within 60 s", () => {
    startStreaming();

    vi.advanceTimersByTime(55_000);
    emit(StreamEvent.AgentStatus, {
      status: "thinking",
      detail: "Processing...",
    });

    vi.advanceTimersByTime(55_000);

    expect(useChat.getState().status).toBe("streaming");
    expect(useChat.getState().error).toBeNull();
  });

  it("message_chunk done=true clears the watchdog entirely", () => {
    startStreaming();

    // Emit a done chunk — should clear (not just reset) the watchdog
    emit(StreamEvent.MessageChunk, {
      message_id: "msg-1",
      delta: " done",
      done: true,
    });

    // Advance well past 60s — no timeout because watchdog was cleared
    vi.advanceTimersByTime(120_000);

    expect(useChat.getState().status).toBe("idle");
    expect(useChat.getState().error).toBeNull();
  });

  it("simulates long tool execution: tool_call → tool_result → chunk → done", () => {
    startStreaming();

    // 0s: streaming starts, watchdog armed (deadline: 60s)

    // 30s: tool_call (e.g., executing a slow command)
    vi.advanceTimersByTime(30_000);
    emit(StreamEvent.ToolCall, {
      tool_call_id: "tc-slow",
      name: "exec_command",
      args: { command: "sleep 90" },
    });
    expect(useChat.getState().status).toBe("streaming");
    // watchdog reset → new deadline: 90s

    // 70s: still running — without the fix, original 60s deadline would have fired
    vi.advanceTimersByTime(40_000);
    expect(useChat.getState().status).toBe("streaming");

    // 85s: tool_result arrives BEFORE the 90s deadline
    vi.advanceTimersByTime(15_000);
    emit(StreamEvent.ToolResult, {
      tool_call_id: "tc-slow",
      result: "command finished",
      error: null,
    });
    // watchdog reset → new deadline: 145s

    // 115s: final chunk with done=true
    vi.advanceTimersByTime(30_000);
    emit(StreamEvent.MessageChunk, {
      message_id: "msg-1",
      delta: " All done!",
      done: true,
    });

    // Should end cleanly — no timeout error at any point
    expect(useChat.getState().status).toBe("idle");
    expect(useChat.getState().error).toBeNull();
    expect(toastErrorSpy).not.toHaveBeenCalled();
  });

  it("multiple tool_call events keep resetting the watchdog", () => {
    startStreaming();

    // Simulate a sequence of tool calls each 45 seconds apart
    for (let i = 1; i <= 4; i++) {
      vi.advanceTimersByTime(45_000);
      emit(StreamEvent.ToolCall, {
        tool_call_id: `tc-${i}`,
        name: `tool_${i}`,
        args: {},
      });
      emit(StreamEvent.ToolResult, {
        tool_call_id: `tc-${i}`,
        result: `result_${i}`,
        error: null,
      });
    }

    // Total elapsed: 180s (3 minutes!) — but no timeout because
    // each event pair reset the watchdog
    expect(useChat.getState().status).toBe("streaming");
    expect(useChat.getState().error).toBeNull();
    expect(toastErrorSpy).not.toHaveBeenCalled();
  });

  it("watchdog does not fire when status is idle (not streaming)", () => {
    // Store is in "idle" state — even if we somehow arm the watchdog,
    // it should not transition to error for idle state
    useChat.setState({ status: "idle" });

    // Advance well past 60s
    vi.advanceTimersByTime(120_000);

    // The watchdog only fires if status is streaming/sending
    expect(useChat.getState().status).toBe("idle");
    expect(useChat.getState().error).toBeNull();
  });
});
