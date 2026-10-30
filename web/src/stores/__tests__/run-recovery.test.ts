import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import type { AgentRun, RunStepsResult } from "../../types/ipc";

const api = vi.hoisted(() => ({
  listRuns: vi.fn(), getRunSteps: vi.fn(), getRunEvents: vi.fn(),
  listMessages: vi.fn(),
  handlers: new Map<string, () => void>(),
  sidecar: null as null | ((event: { status: string }) => void),
}));
vi.mock("../../ipc", () => ({
  typedIPC: api,
  ipc: {
    on: (event: string, fn: () => void) => {
      api.handlers.set(event, fn);
      return () => api.handlers.delete(event);
    },
    onSideCar: (fn: typeof api.sidecar) => { api.sidecar = fn; return () => { api.sidecar = null; }; },
  },
}));
import { useRunTimelineStore as store } from "../runStore";
import { useSessionStore } from "../sessionStore";
import { useChat } from "../chat";
import { usePermissionStore } from "../permissionStore";
import { StreamEvent } from "../../types/ipc";

const run: AgentRun = {
  id: "r1", session_id: "s1", mode: "chat", status: "running", title: "Work",
  created_at: "2026-10-23T00:00:00Z",
};
const snapshot = (row = run, cursor = 1): RunStepsResult => ({ run: row, cursor, steps: [] });
const idle = () => waitFor(() => expect(store.getState().loading).toBe(false));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(async () => {
  await idle();
  store.getState().reset();
  useChat.getState().reset();
  usePermissionStore.setState({ pending: {}, resolving: {} });
  vi.resetAllMocks();
  useSessionStore.setState({ currentSessionId: "s1" });
  api.listRuns.mockResolvedValue({ runs: [run], active_truncated: false });
  api.getRunSteps.mockResolvedValue(snapshot());
  api.getRunEvents.mockResolvedValue({ events: [], next_seq: 1, has_more: false });
  api.listMessages.mockResolvedValue({ messages: [] });
});

describe("durable run recovery", () => {
  it("reconnects from a fresh snapshot even without a final notification", async () => {
    await store.getState().loadForSession("s1");
    api.getRunSteps.mockResolvedValue(snapshot({ ...run, status: "completed" }, 4));
    api.sidecar?.({ status: "started" });
    await idle();
    expect(store.getState().runs.r1.status).toBe("completed");
    expect(api.getRunSteps).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(api.listMessages).toHaveBeenCalledWith("s1", { limit: 500 }));
  });

  it("backfills an active run's durable tool result after reconnect", async () => {
    await store.getState().loadForSession("s1");
    await waitFor(() => expect(api.listMessages).toHaveBeenCalledTimes(1));
    api.listMessages.mockResolvedValue({ messages: [{ id: "saved-tool", role: "tool", text: "persisted output",
      tool_call_id: "call-1", tool_name: "read_file", streaming: false, created_at: 1, metadata: { run_id: "r1" } }] });
    api.sidecar?.({ status: "started" });
    await waitFor(() => expect(useChat.getState().messages.find((message) => message.id === "tr-call-1")?.text).toBe("persisted output"));
    expect(store.getState().runs.r1.status).toBe("running");
  });

  it("restores a running stream from the reconnect snapshot", async () => {
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), live_stream: { complete: true,
      messages: [{ message_id: "streamed", seq: 2, text: "previous output", done: false }] } });
    await store.getState().loadForSession("s1");
    expect(useChat.getState().messages.find((message) => message.id === "streamed")?.text).toBe("previous output");
    expect(useChat.getState().status).toBe("streaming");
  });

  it("restores active status before any stream text exists", async () => {
    await store.getState().loadForSession("s1");
    expect(useChat.getState().status).toBe("streaming");
    expect(useChat.getState().messages).toEqual([]);
  });

  it("waits for history loading before restoring active status", async () => {
    const history = deferred<{ messages: never[] }>();
    api.listMessages.mockReturnValueOnce(history.promise);
    const loading = useChat.getState().loadMessages("s1");
    await store.getState().loadForSession("s1");
    expect(useChat.getState().status).toBe("idle");
    history.resolve({ messages: [] });
    await loading;
    expect(useChat.getState().status).toBe("streaming");
  });

  it("does not restore an active status after that run finishes during history loading", async () => {
    const history = deferred<{ messages: never[] }>();
    api.listMessages.mockReturnValueOnce(history.promise);
    const loading = useChat.getState().loadMessages("s1");
    await store.getState().loadForSession("s1");
    api.getRunSteps.mockResolvedValue(snapshot({ ...run, status: "completed" }, 2));
    api.sidecar?.({ status: "started" });
    await idle();
    history.resolve({ messages: [] });
    await loading;
    expect(useChat.getState().status).toBe("idle");
  });

  it("restores a live question from a reconnect snapshot", async () => {
    const question = { request_id: "ask-1", session_id: "s1", run_id: "r1", timeout_s: 60,
      expires_at_ms: Date.now() + 60000, questions: [] };
    api.getRunSteps.mockResolvedValue({ ...snapshot({ ...run, status: "waiting_input" }), pending_ask_user: question });
    await store.getState().loadForSession("s1");
    expect(useChat.getState().askUser).toEqual(question);
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_ask_user: null });
    api.sidecar?.({ status: "started" });
    await idle();
    expect(useChat.getState().askUser).toBeNull();
  });

  it("finds a live question after an earlier run with no pending question", async () => {
    const older = { ...run, id: "r2", status: "waiting_input" as const };
    const question = { request_id: "ask-2", session_id: "s1", run_id: "r2", timeout_s: 60,
      remaining_ms: 60000, questions: [] };
    api.listRuns.mockResolvedValue({ runs: [run, older], active_truncated: false });
    api.getRunSteps.mockImplementation(async (id: string) => id === "r1"
      ? { ...snapshot(run), pending_ask_user: null }
      : { ...snapshot(older), pending_ask_user: question });
    await store.getState().loadForSession("s1");
    expect(useChat.getState().askUser).toEqual(question);
  });

  it("restores only this session's live Native permissions while retaining other sessions and Codex", async () => {
    const native = { request_id: "perm-native", session_id: "s1", run_id: "r1", tool: "write_file", args: {}, remaining_ms: 60000 };
    usePermissionStore.getState()._enqueueRequest({ request_id: "other", session_id: "s2", run_id: "other-run", tool: "exec", args: {} });
    usePermissionStore.getState()._enqueueRequest({ request_id: "codex", session_id: "s1", run_id: "r-codex", tool: "codex:exec", args: {} });
    usePermissionStore.getState()._enqueueRequest({ request_id: "outside-window", session_id: "s1", run_id: "old-run", tool: "exec", args: {} });
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_native_permissions: [native] });
    await store.getState().loadForSession("s1");
    expect(Object.keys(usePermissionStore.getState().pending).sort()).toEqual(["codex", "other", "outside-window", "perm-native"]);
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_native_permissions: [] });
    api.sidecar?.({ status: "started" });
    await idle();
    expect(Object.keys(usePermissionStore.getState().pending).sort()).toEqual(["codex", "other", "outside-window"]);
  });

  it("does not revive an approval resolved while the snapshot is in flight", async () => {
    const pending = deferred<RunStepsResult>();
    api.getRunSteps.mockReturnValueOnce(pending.promise);
    const loading = store.getState().loadForSession("s1");
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalled());
    usePermissionStore.getState()._markResolved({ request_id: "perm-closed", decision: "deny" });
    pending.resolve({ ...snapshot(run), pending_native_permissions: [{ request_id: "perm-closed", session_id: "s1",
      run_id: "r1", tool: "exec", args: {}, remaining_ms: 60000 }] });
    await loading;
    expect(usePermissionStore.getState().pending["perm-closed"]).toBeUndefined();
  });

  it("keeps a newer live approval when an older empty snapshot returns", async () => {
    const pending = deferred<RunStepsResult>();
    api.getRunSteps.mockReturnValueOnce(pending.promise);
    const loading = store.getState().loadForSession("s1");
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalled());
    usePermissionStore.getState()._enqueueRequest({ request_id: "new-live", session_id: "s1", run_id: "r1", tool: "exec", args: {} });
    pending.resolve({ ...snapshot(run), pending_native_permissions: [] });
    await loading;
    expect(usePermissionStore.getState().pending["new-live"]).toBeDefined();
  });

  it("restores Codex approval explicitly and removes it when its run no longer waits", async () => {
    const request = { request_id: "codex:r1:token:1", session_id: "s1", run_id: "r1", tool: "codex:exec", args: { command: "echo x" }, remaining_ms: 60000 };
    usePermissionStore.getState().setAlwaysAllow(true);
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_native_permissions: [], pending_codex_permissions: [request] });
    await store.getState().loadForSession("s1");
    expect(usePermissionStore.getState().pending[request.request_id]).toMatchObject(request);
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_native_permissions: [], pending_codex_permissions: [] });
    api.sidecar?.({ status: "started" });
    await idle();
    expect(usePermissionStore.getState().pending[request.request_id]).toBeUndefined();
    usePermissionStore.getState().setAlwaysAllow(false);
  });

  it("rejects a Codex snapshot bound to another session", async () => {
    api.getRunSteps.mockResolvedValue({ ...snapshot(run), pending_codex_permissions: [{ request_id: "codex:foreign", session_id: "s2", run_id: "r1", tool: "codex:exec", args: {} }] });
    await store.getState().loadForSession("s1");
    expect(store.getState().error).toContain("Invalid run snapshot");
    expect(usePermissionStore.getState().pending["codex:foreign"]).toBeUndefined();
  });

  it("does not revive a Codex approval closed during the snapshot read", async () => {
    const pending = deferred<RunStepsResult>();
    api.getRunSteps.mockReturnValueOnce(pending.promise);
    const loading = store.getState().loadForSession("s1");
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalled());
    usePermissionStore.getState()._markResolved({ request_id: "codex:r1:closed", decision: "deny" });
    pending.resolve({ ...snapshot(run), pending_codex_permissions: [{ request_id: "codex:r1:closed", session_id: "s1",
      run_id: "r1", tool: "codex:exec", args: {}, remaining_ms: 60000 }] });
    await loading;
    expect(usePermissionStore.getState().pending["codex:r1:closed"]).toBeUndefined();
  });

  it("pages ordered events and applies the final state rather than a stale notification", async () => {
    await store.getState().loadForSession("s1");
    const step = { id: "st", run_id: "r1", session_id: "s1", kind: "tool_call",
      status: "completed", title: "check", summary: "ok", started_at: run.created_at, ordinal: 1 };
    api.getRunEvents.mockResolvedValueOnce({ events: [
      { run_id: "r1", seq: 2, data: { step } },
    ], next_seq: 2, has_more: true }).mockResolvedValueOnce({ events: [
      { run_id: "r1", seq: 3, data: { run: { ...run, status: "failed", error: "check failed" } } },
    ], next_seq: 3, has_more: false });
    api.handlers.get(StreamEvent.RunCompleted)?.();
    await idle();
    expect(store.getState().runs.r1.status).toBe("failed");
    expect(store.getState().runs.r1.steps).toEqual([step]);
    expect(api.getRunEvents.mock.calls.map((args) => args[1].after_seq)).toEqual([1, 2]);
  });

  it("refreshes after an invalidated cursor or sequence gap", async () => {
    await store.getState().loadForSession("s1");
    api.getRunEvents.mockRejectedValueOnce(Object.assign(new Error("cursor ahead"), { code: -32602 }));
    api.getRunSteps.mockResolvedValue(snapshot(run, 0));
    api.handlers.get(StreamEvent.RunCompleted)?.();
    await idle();
    api.getRunEvents.mockResolvedValueOnce({ events: [{ run_id: "r1", seq: 9, data: {} }], next_seq: 9, has_more: false });
    api.getRunSteps.mockResolvedValue(snapshot({ ...run, status: "cancelled" }, 9));
    api.handlers.get(StreamEvent.RunCompleted)?.();
    await idle();
    expect(api.getRunSteps).toHaveBeenCalledTimes(3);
    expect(store.getState().runs.r1.status).toBe("cancelled");
  });

  it("queues notifications during loading so an older snapshot cannot hide completion", async () => {
    const pending = deferred<RunStepsResult>();
    api.getRunSteps.mockReturnValueOnce(pending.promise);
    const loading = store.getState().loadForSession("s1");
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalledTimes(1));
    api.getRunEvents.mockResolvedValue({ events: [{ run_id: "r1", seq: 2,
      data: { run: { ...run, status: "completed" } } }], next_seq: 2, has_more: false });
    api.handlers.get(StreamEvent.RunCompleted)?.();
    pending.resolve(snapshot());
    await loading;
    expect(store.getState().runs.r1.status).toBe("completed");
  });

  it("discards late data and errors after switching sessions", async () => {
    const pending = deferred<RunStepsResult>();
    api.getRunSteps.mockReturnValueOnce(pending.promise);
    const first = store.getState().loadForSession("s1");
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalledTimes(1));
    useSessionStore.setState({ currentSessionId: "s2" });
    api.listRuns.mockResolvedValue({ runs: [], active_truncated: false });
    const second = store.getState().loadForSession("s2");
    expect(store.getState().runs).toEqual({});
    pending.reject(new Error("old session error"));
    await Promise.all([first, second]);
    expect(store.getState().error).toBeNull();
    expect(store.getState().runs).toEqual({});
  });

  it("keeps visible history and reports fetch failure instead of silently erasing steps", async () => {
    await store.getState().loadForSession("s1");
    api.getRunSteps.mockRejectedValue(new Error("database unavailable"));
    await store.getState().loadForSession("s1");
    expect(store.getState().runs.r1.title).toBe("Work");
    expect(store.getState().error).toBe("database unavailable");
    useSessionStore.setState({ currentSessionId: null });
    await store.getState().loadForSession(null);
    expect(store.getState().runs).toEqual({});
  });

  it("does not invalidate pending conversation recovery on duplicate unchanged wakeups", async () => {
    const pending = deferred<{ messages: import("../../types/ipc").Message[] }>();
    api.getRunSteps.mockResolvedValue(snapshot({ ...run, status: "completed" }));
    api.listMessages.mockReturnValueOnce(pending.promise);
    await store.getState().loadForSession("s1");
    await waitFor(() => expect(api.listMessages).toHaveBeenCalledTimes(1));
    api.handlers.get(StreamEvent.RunCompleted)?.();
    await idle();
    pending.resolve({ messages: [{ id: "saved", role: "assistant", text: "recovered",
      streaming: false, created_at: 1 }] });
    await waitFor(() => expect(useChat.getState().messages[0]?.id).toBe("saved"));
    expect(api.listMessages).toHaveBeenCalledTimes(1);
  });

  it("rejects cross-session snapshots and coalesces a notification burst", async () => {
    await store.getState().loadForSession("s1");
    for (let i = 0; i < 50; i++) api.handlers.get(StreamEvent.RunStepStarted)?.();
    await idle();
    expect(api.getRunEvents).toHaveBeenCalledTimes(1);
    api.getRunSteps.mockResolvedValue(snapshot({ ...run, session_id: "s-other" }));
    await store.getState().loadForSession("s1");
    expect(store.getState().runs.r1.session_id).toBe("s1");
    expect(store.getState().error).toContain("Invalid run snapshot");
  });
});
