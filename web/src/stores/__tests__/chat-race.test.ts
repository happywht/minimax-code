/**
 * Tests for P2#33: session quick-switch race condition.
 *
 * Verifies that loadMessages uses a monotonic sequence counter
 * so stale responses don't overwrite the current session's messages.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useChat } from "../chat";
import { useSessionStore } from "../sessionStore";
import { typedIPC } from "../../ipc";
import type { Message } from "../../types/ipc";

// Mock the IPC module
vi.mock("../../ipc", () => ({
  ipc: {
    start: vi.fn().mockResolvedValue(undefined),
    on: vi.fn().mockReturnValue(() => {}),
    ping: vi.fn().mockResolvedValue(true),
  },
  IPCError: class extends Error {},
  typedIPC: {
    listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    createSession: vi.fn(),
    listMessages: vi.fn(),
    sendMessage: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    deleteSession: vi.fn(),
    updateSession: vi.fn(),
    cancelAgent: vi.fn(),
  },
}));

describe("P2#33: session quick-switch race condition", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset stores
    useChat.getState().reset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      loading: false,
      filter: "all",
    });
  });

  it("uses the selected engine when the first message creates a session", async () => {
    const originalCreate = useSessionStore.getState().create;
    const create = vi.fn().mockRejectedValue(new Error("stop after session request"));
    useSessionStore.setState({ currentSessionId: null, newSessionEngine: "codex", create });
    try {
      await useChat.getState().send("new task");
      expect(create).toHaveBeenCalledWith("new task", undefined, "codex");
    } finally {
      useSessionStore.setState({ create: originalCreate, newSessionEngine: "native" });
    }
  });

  it("keeps a cancelled response cancelled instead of marking it completed", async () => {
    useSessionStore.setState({ currentSessionId: "cancelled-session" });
    vi.mocked(typedIPC.sendMessage).mockResolvedValueOnce({
      session_id: "cancelled-session", message_id: "cancelled-message", text: "", cancelled: true,
    });
    await useChat.getState().send("cancel this task");
    const assistant = useChat.getState().messages.find((message) => message.role === "assistant");
    expect(assistant?.status).toBe("cancelled");
    expect(assistant?.streaming).toBe(false);
    expect(useChat.getState().status).toBe("idle");
  });

  it("waits for run cleanup after cancellation acknowledgement", async () => {
    useSessionStore.setState({ currentSessionId: "waiting-cancel" });
    let finish!: (value: any) => void;
    vi.mocked(typedIPC.sendMessage).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    vi.mocked(typedIPC.cancelAgent).mockResolvedValueOnce({ ok: true });
    const sending = useChat.getState().send("long task");
    const cancelling = useChat.getState().cancel();
    await Promise.resolve();
    expect(useChat.getState().status).toBe("cancelling");
    expect(useChat.getState().messages.find((m) => m.role === "assistant")?.status).toBe("cancelling");
    finish({ session_id: "waiting-cancel", message_id: "cancelled", text: "", cancelled: true });
    await Promise.all([sending, cancelling]);
    expect(useChat.getState().status).toBe("idle");
    expect(useChat.getState().messages.find((m) => m.role === "assistant")?.status).toBe("cancelled");
  });

  it("does not report cancellation when the cancel request fails", async () => {
    useSessionStore.setState({ currentSessionId: "cancel-failed" });
    let finish!: (value: any) => void;
    vi.mocked(typedIPC.sendMessage).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    vi.mocked(typedIPC.cancelAgent).mockRejectedValueOnce(new Error("connection failed"));
    const sending = useChat.getState().send("keep working");
    await useChat.getState().cancel();
    expect(useChat.getState().status).toBe("sending");
    expect(useChat.getState().messages.find((m) => m.role === "assistant")?.status).toBe("queued");
    finish({ session_id: "cancel-failed", message_id: "done", text: "completed", cancelled: false });
    await sending;
    expect(useChat.getState().messages.find((m) => m.role === "assistant")?.status).toBe("completed");
  });

  it("stale loadMessages should not overwrite newer session data", async () => {
    const sessionA = "session-a";
    const sessionB = "session-b";

    const messagesA: Message[] = [
      { id: "msg-a1", role: "user", text: "Hello A", streaming: false, created_at: 1 },
    ];
    const messagesB: Message[] = [
      { id: "msg-b1", role: "user", text: "Hello B", streaming: false, created_at: 2 },
    ];

    // Make listMessages return different results with different delays
    vi.mocked(typedIPC.listMessages).mockImplementation(async (sid: string) => {
      if (sid === sessionA) {
        // Simulate slow response for session A
        await new Promise((r) => setTimeout(r, 100));
        return { messages: messagesA };
      }
      // Fast response for session B
      return { messages: messagesB };
    });

    // Switch to session A (slow)
    useSessionStore.setState({ currentSessionId: sessionA });
    const loadPromiseA = useChat.getState().loadMessages(sessionA);

    // Immediately switch to session B (fast) — this should win
    useSessionStore.setState({ currentSessionId: sessionB });
    await useChat.getState().loadMessages(sessionB);

    // Wait for session A's response to arrive
    await loadPromiseA;

    // The store should contain session B's messages, not A's
    const msgs = useChat.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe("msg-b1");
    expect(msgs[0].text).toBe("Hello B");
  });

  it("rapid switching keeps only the latest session's messages", async () => {
    const sessions = ["s1", "s2", "s3", "s4", "s5"];
    const loadData: Record<string, any[]> = {
      s1: [{ id: "m1", role: "user", text: "Session 1", streaming: false, created_at: 1 }],
      s2: [{ id: "m2", role: "user", text: "Session 2", streaming: false, created_at: 2 }],
      s3: [{ id: "m3", role: "user", text: "Session 3", streaming: false, created_at: 3 }],
      s4: [{ id: "m4", role: "user", text: "Session 4", streaming: false, created_at: 4 }],
      s5: [{ id: "m5", role: "user", text: "Session 5", streaming: false, created_at: 5 }],
    };

    vi.mocked(typedIPC.listMessages).mockImplementation(async (sid: string) => {
      return { messages: loadData[sid] || [] };
    });

    // Rapid-fire switch through all sessions
    const promises = sessions.map((sid) => {
      useSessionStore.setState({ currentSessionId: sid });
      return useChat.getState().loadMessages(sid);
    });
    await Promise.all(promises);

    // Only the last session's messages should be present
    const msgs = useChat.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe("m5");
    expect(msgs[0].text).toBe("Session 5");
  });

  it("error from stale request is silently ignored", async () => {
    const sessionA = "session-a-err";
    const sessionB = "session-b-ok";

    vi.mocked(typedIPC.listMessages).mockImplementation(async (sid: string) => {
      if (sid === sessionA) {
        await new Promise((r) => setTimeout(r, 100));
        throw new Error("Network error for A");
      }
      return { messages: [{ id: "m-b", role: "user", text: "B ok", streaming: false, created_at: 1 }] };
    });

    useSessionStore.setState({ currentSessionId: sessionA });
    const loadPromiseA = useChat.getState().loadMessages(sessionA);
    useSessionStore.setState({ currentSessionId: sessionB });
    await useChat.getState().loadMessages(sessionB);
    await loadPromiseA;

    // Should have B's messages, no error state
    const state = useChat.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].text).toBe("B ok");
    expect(state.error).toBeNull();
  });

  it("preserves persisted tool details when loading history", async () => {
    vi.mocked(typedIPC.listMessages).mockResolvedValueOnce({
      messages: [
        {
          id: "tool-1",
          role: "tool",
          text: "file contents",
          created_at: 1,
          tool_call_id: "call_read_1",
          tool_name: "read_file",
          tool_args: { path: "app.py", start: 1 },
        },
      ],
    });

    useSessionStore.setState({ currentSessionId: "session-with-tools" });
    await useChat.getState().loadMessages("session-with-tools");

    const [toolMessage] = useChat.getState().messages;
    expect(toolMessage.tool_call_id).toBe("call_read_1");
    expect(toolMessage.tool_name).toBe("read_file");
    expect(toolMessage.tool_args).toEqual({ path: "app.py", start: 1 });
  });

  it("does not let history reload clear an active send", async () => {
    useSessionStore.setState({ currentSessionId: "session-active" });
    useChat.setState({
      status: "sending",
      error: null,
      messages: [
        {
          id: "user-local",
          role: "user",
          text: "local prompt still sending",
          streaming: false,
          created_at: 1,
        },
      ],
    });
    vi.mocked(typedIPC.listMessages).mockResolvedValueOnce({ messages: [] });

    await useChat.getState().loadMessages("session-active");

    const state = useChat.getState();
    expect(state.status).toBe("sending");
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].text).toBe("local prompt still sending");
  });

  it.each(["idle", "streaming", "cancelling"] as const)("retains cached history and %s state when refresh fails", async (status) => {
    useSessionStore.setState({ currentSessionId: "refresh-failed" });
    const messages: Message[] = [{ id: "saved", role: "assistant", text: "existing work",
      created_at: 1, streaming: status === "streaming" }];
    useChat.setState({ messages, status });
    vi.mocked(typedIPC.listMessages).mockRejectedValueOnce(new Error("network unavailable"));
    await useChat.getState().loadMessages("refresh-failed");
    expect(useChat.getState().messages).toEqual(messages);
    expect(useChat.getState().status).toBe(status);
    expect(useChat.getState().loadingMessages).toBe(false);
  });

  it("does not turn pending cancellation into idle when history refresh succeeds", async () => {
    useSessionStore.setState({ currentSessionId: "cancel-refresh" });
    useChat.setState({ status: "cancelling", messages: [{ id: "pending", role: "assistant",
      text: "stopping", created_at: 1, streaming: true, status: "cancelling" }] });
    vi.mocked(typedIPC.listMessages).mockResolvedValueOnce({ messages: [] });
    await useChat.getState().loadMessages("cancel-refresh");
    expect(useChat.getState().status).toBe("cancelling");
    expect(useChat.getState().messages[0].id).toBe("pending");
  });

  it("ignores a stale session refresh that predates creating the current session", async () => {
    let resolveList: (value: { sessions: any[]; total: number }) => void = () => {};
    vi.mocked(typedIPC.listSessions).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveList = resolve;
        }),
    );
    vi.mocked(typedIPC.createSession).mockResolvedValueOnce({
      session_id: "ses_new",
      session: {
        id: "ses_new",
        title: "New task",
        archived: false,
        created_at: 1,
        updated_at: 1,
        model_id: null,
        workspace_mode: "local",
      },
      reused: false,
    });

    const refreshPromise = useSessionStore.getState().refresh();
    await useSessionStore.getState().create("New task");
    useChat.setState({
      status: "sending",
      error: null,
      messages: [
        {
          id: "user-local",
          role: "user",
          text: "new session prompt",
          streaming: false,
          created_at: 1,
        },
      ],
    });
    resolveList({ sessions: [], total: 0 });
    await refreshPromise;

    expect(useSessionStore.getState().currentSessionId).toBe("ses_new");
    expect(useChat.getState().messages[0].text).toBe("new session prompt");
  });
});
