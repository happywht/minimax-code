import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { StreamEvent, type AgentRun, type Message, type SendMessageResult } from "../../types/ipc";

const api = vi.hoisted(() => ({
  listMessages: vi.fn(), sendMessage: vi.fn(), updateMessage: vi.fn(), listRuns: vi.fn(),
  handlers: new Map<string, (env: { data: any }) => void>(),
}));
vi.mock("../../ipc", () => ({
  IPCError: class extends Error {},
  typedIPC: api,
  ipc: { start: vi.fn(), ping: vi.fn().mockResolvedValue(true),
    onSideCar: () => () => {},
    on: (event: string, fn: (env: { data: any }) => void) => {
      api.handlers.set(event, fn); return () => api.handlers.delete(event);
    } },
}));
import { useChat as store } from "../chat";
import { useSessionStore } from "../sessionStore";

const run: AgentRun = { id: "r", session_id: "s", mode: "chat", status: "completed",
  title: "task", created_at: "2026-10-23T00:00:00Z", request_key: "accepted",
  user_message_id: "user", assistant_message_id: "answer" };
const messages: Message[] = [
  { id: "user", role: "user", text: "task", streaming: false, created_at: 1, metadata: { run_id: "r" } },
  { id: "answer", role: "assistant", text: "complete answer", streaming: false, created_at: 2, metadata: { run_id: "r" } },
];
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const recover = (rows = [run]) => store.getState().reconcileRuns(rows, () => true);
const emit = (event: string, data: Record<string, unknown>) =>
  api.handlers.get(event)?.({ data: { session_id: "s", run_id: "r", ...data } });

beforeAll(() => store.getState().init());
beforeEach(() => {
  store.getState().reset();
  vi.clearAllMocks();
  useSessionStore.setState({ currentSessionId: "s", sessions: [] });
  api.listMessages.mockResolvedValue({ messages });
  api.listRuns.mockResolvedValue({ runs: [], active_truncated: false });
});
afterEach(() => store.getState().reset());

describe("conversation reconciliation from durable runs", () => {
  it("resumes sequenced live text without duplicate or out-of-order chunks", () => {
    const active = { ...run, status: "running" as const };
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 2, text: "ab", done: false }] });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_seq: 4, delta: "d", done: false });
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 3, text: "abc", done: false }] });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_seq: 4, delta: "d", done: false });
    expect(store.getState().messages.find((message) => message.id === "answer")?.text).toBe("abcd");
  });

  it("continues live text after snapshot overflow without replay duplicates", () => {
    const active = { ...run, status: "running" as const };
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 2, text: "ab", done: false }] });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_unavailable: true, stream_event_seq: 3, delta: "c", done: false });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_unavailable: true, stream_event_seq: 3, delta: "c", done: false });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_unavailable: true, stream_event_seq: 4, delta: "d", done: false });
    emit(StreamEvent.MessageChunk, { message_id: "answer", stream_seq: 3, delta: "old", done: false });
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 2, text: "stale", done: false }] });
    expect(store.getState().messages.find((message) => message.id === "answer")?.text).toBe("abcd");
  });

  it("preserves local edits and another session during live snapshot recovery", async () => {
    const active = { ...run, status: "running" as const };
    store.setState({ messages: [{ id: "answer", role: "assistant", text: "original", streaming: false, created_at: 1, metadata: { run_id: "r" } }] });
    api.updateMessage.mockResolvedValueOnce({});
    await store.getState().updateMessage("answer", "local edit");
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 1, text: "server", done: false }] });
    expect(store.getState().messages[0].text).toBe("local edit");
    store.getState().reset();
    useSessionStore.setState({ currentSessionId: "other" });
    store.getState().addLocalMessage("other session");
    store.getState().reconcileLiveStream(active, { complete: true, messages: [{ message_id: "answer", seq: 1, text: "server", done: false }] });
    expect(store.getState().messages.map((message) => message.text)).toEqual(["other session"]);
  });

  it("places an omitted assistant segment before its tool and preserves unconfirmed input", () => {
    store.setState({ messages: [
      { id: "tc-1", role: "tool", parent_id: "answer", text: "tool", streaming: false, created_at: 1 },
      { id: "unsent", role: "user", text: "draft", streaming: false, created_at: 2, metadata: { request_key: "local" } },
    ] });
    store.getState().reconcileLiveStream({ ...run, status: "running" }, { complete: true,
      messages: [{ message_id: "answer", seq: 1, text: "before tool", done: true }] });
    expect(store.getState().messages.map((message) => message.id)).toEqual(["answer", "tc-1", "unsent"]);
  });
  it("keeps a Native answer active while its final validation receipt is pending", async () => {
    const receipt = deferred<SendMessageResult>();
    api.sendMessage.mockReturnValueOnce(receipt.promise);
    const sending = store.getState().send("validate work");
    emit(StreamEvent.MessageChunk, { message_id: "answer", delta: "saved answer", done: true });
    expect(store.getState().messages.find((message) => message.id === "answer")).toMatchObject({ streaming: true, status: "streaming" });
    expect(store.getState().status).toBe("streaming");
    receipt.resolve({ session_id: "s", message_id: "answer", run_id: "r", text: "saved answer", cancelled: false,
      run_status: "completed", verification: { version: 1, verdict: "passed", reason: "validation_passed", requires_review: false } });
    await sending;
    expect(store.getState().messages.find((message) => message.id === "answer")).toMatchObject({ streaming: false, status: "completed",
      metadata: { verification: { verdict: "passed" } } });
  });

  it("restores an offline final transcript and ignores all late notifications for that run", async () => {
    store.setState({ status: "streaming" });
    await recover();
    const restored = store.getState().messages;
    expect(restored.map((m) => m.id)).toEqual(["user", "answer"]);
    expect(store.getState().status).toBe("idle");
    emit(StreamEvent.MessageChunk, { message_id: "answer", delta: "duplicate", done: false });
    emit(StreamEvent.ToolCall, { tool_call_id: "old-tool", name: "exec", args: {} });
    emit(StreamEvent.ToolResult, { tool_call_id: "old-tool", result: "late" });
    emit(StreamEvent.AgentStatus, { status: "error", detail: "stale failure" });
    emit(StreamEvent.AskUser, { request_id: "old-question", questions: [], timeout_s: 600 });
    expect(store.getState().messages).toEqual(restored);
    expect(store.getState().status).toBe("idle");
    expect(store.getState().askUser).toBeNull();
    emit(StreamEvent.MessageChunk, { run_id: "new", message_id: "new-answer", delta: "new", done: false });
    expect(store.getState().messages.at(-1)?.text).toBe("new");
    expect(store.getState().status).toBe("streaming");
  });

  it("replaces confirmed provisional messages and keeps unconfirmed local input", async () => {
    store.setState({ status: "error", error: "offline", messages: [
      { id: "local-user", role: "user", text: "task", streaming: false, created_at: 1, metadata: { request_key: "accepted" } },
      { id: "local-answer", role: "assistant", text: "offline", streaming: false, created_at: 2, metadata: { request_key: "accepted" } },
      { id: "unsent", role: "user", text: "do not lose", streaming: false, created_at: 3, metadata: { request_key: "unsent-key" } },
    ] });
    await recover();
    expect(store.getState().messages.map((m) => m.id)).toEqual(["user", "answer", "unsent"]);
    expect(store.getState().messages[0].render_key).toBe("local-user");
    expect(store.getState().error).toBe("offline");
    await recover();
    expect(store.getState().messages[0].render_key).toBe("local-user");
  });

  it.each(["failed", "cancelled", "interrupted"] as const)("retains unpersisted partial output as %s", async (status) => {
    store.setState({ messages: [{ id: "partial", role: "assistant", text: "partial evidence",
      created_at: 3, streaming: true, metadata: { run_id: "r" } }] });
    await recover([{ ...run, status, error: "unfinished" }]);
    expect(store.getState().messages.at(-1)).toMatchObject({ id: "partial", text: "partial evidence",
      streaming: false, status: status === "cancelled" ? "cancelled" : "failed" });
    expect(store.getState().status).toBe(status === "cancelled" ? "idle" : "error");
  });

  it("restores a settled budget pause, preserves partial metadata, and releases the composer", async () => {
    store.setState({ status: "streaming" });
    api.listMessages.mockResolvedValueOnce({ messages: [{ ...messages[1], status: "failed",
      metadata: { engine: "codex", partial: true, usage_certainty: "unknown", tokens_in: null } }] });
    await recover([{ ...run, status: "budget_paused" }]);
    expect(api.listMessages).toHaveBeenCalledWith("s", { limit: 500 });
    expect(store.getState().status).toBe("idle");
    expect(store.getState().messages[0].metadata?.tokens_in).toBeNull();
    expect(store.getState().messages[0].metadata?.partial).toBe(true);
  });

  it.each(["running", "queued", "waiting_input"] as const)("does not replace an active %s conversation", async (status) => {
    await recover([{ ...run, status }]);
    expect(api.listMessages).not.toHaveBeenCalled();
  });

  it("restores missing persisted tool results during an active run without replacing live state", async () => {
    const active = { ...run, status: "running" as const };
    const pending = deferred<{ messages: Message[] }>();
    api.listMessages.mockReturnValueOnce(pending.promise);
    store.setState({ status: "streaming", messages: [
      { id: "tc-t1", role: "tool", text: "call", tool_call_id: "t1", streaming: false, created_at: 1 },
      { id: "live", role: "assistant", text: "partial", streaming: true, created_at: 2 },
    ] });
    const syncing = store.getState().reconcileRuns([active], () => true, true);
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());
    store.getState().addLocalMessage("keep my input");
    pending.resolve({ messages: [
      { id: "saved-tool", role: "tool", text: "result", tool_call_id: "t1", tool_name: "read_file", streaming: false, created_at: 1, metadata: { run_id: "r" } },
      { id: "foreign", role: "tool", text: "other run", tool_call_id: "t2", streaming: false, created_at: 1, metadata: { run_id: "other" } },
    ] });
    await syncing;
    expect(store.getState().messages.map((message) => message.id)).toEqual(["tc-t1", "tr-t1", "live", expect.stringMatching(/^user-/)]);
    expect(store.getState().messages[1]).toMatchObject({ text: "result", tool_name: "read_file" });
    expect(store.getState().status).toBe("streaming");
    emit(StreamEvent.ToolResult, { tool_call_id: "t1", result: "late duplicate" });
    expect(store.getState().messages.filter((message) => message.id === "tr-t1")).toHaveLength(1);
    expect(store.getState().messages.find((message) => message.id === "tr-t1")?.text).toBe("result");
  });

  it("discards active results after switching sessions", async () => {
    const pending = deferred<{ messages: Message[] }>();
    api.listMessages.mockReturnValueOnce(pending.promise);
    const syncing = store.getState().reconcileRuns([{ ...run, status: "running" }], () => true, true);
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());
    store.getState().reset();
    useSessionStore.setState({ currentSessionId: "other" });
    store.getState().addLocalMessage("other conversation");
    pending.resolve({ messages: [{ id: "saved", role: "tool", text: "result", tool_call_id: "t", streaming: false, created_at: 1, metadata: { run_id: "r" } }] });
    await syncing;
    expect(store.getState().messages.map((message) => message.text)).toEqual(["other conversation"]);
  });

  it("discards history after switching sessions", async () => {
    const pending = deferred<{ messages: Message[] }>();
    api.listMessages.mockReturnValueOnce(pending.promise);
    const syncing = recover();
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());
    store.getState().reset();
    useSessionStore.setState({ currentSessionId: "other" });
    store.getState().addLocalMessage("other conversation");
    pending.resolve({ messages });
    await syncing;
    expect(store.getState().messages.map((m) => m.text)).toEqual(["other conversation"]);
  });

  it("discards history when a new local input or a different run arrives during the read", async () => {
    for (const action of [() => store.getState().addLocalMessage("new local input"),
      () => emit(StreamEvent.ToolCall, { run_id: "new", tool_call_id: "new-tool", name: "read", args: {} })]) {
      const pending = deferred<{ messages: Message[] }>();
      api.listMessages.mockReturnValueOnce(pending.promise);
      const syncing = recover();
      await waitFor(() => expect(api.listMessages).toHaveBeenCalled());
      action();
      const current = store.getState().messages;
      pending.resolve({ messages });
      await syncing;
      expect(store.getState().messages).toEqual(current);
      api.listMessages.mockClear();
    }
  });

  it("allows late packets from the same completed run while fetching its authoritative history", async () => {
    const pending = deferred<{ messages: Message[] }>();
    api.listMessages.mockReturnValueOnce(pending.promise);
    const syncing = recover();
    await waitFor(() => expect(api.listMessages).toHaveBeenCalled());
    emit(StreamEvent.MessageChunk, { message_id: "answer", delta: "partial", done: false });
    pending.resolve({ messages });
    await syncing;
    expect(store.getState().messages.at(-1)?.text).toBe("complete answer");
  });

  it("waits for the pending HTTP reply and then removes provisional duplicates", async () => {
    const pending = deferred<SendMessageResult>();
    api.sendMessage.mockReturnValueOnce(pending.promise);
    const sending = store.getState().send("task");
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalled());
    const key = api.sendMessage.mock.calls[0][0].idempotency_key;
    const syncing = recover([{ ...run, request_key: key }]);
    expect(api.listMessages).not.toHaveBeenCalled();
    pending.resolve({ session_id: "s", message_id: "answer", text: "complete answer" });
    await Promise.all([sending, syncing]);
    expect(store.getState().messages.map((m) => m.id)).toEqual(["user", "answer"]);
  });

  it("preserves history on a read failure and retries on a later reconciliation", async () => {
    store.getState().addLocalMessage("local");
    const before = store.getState().messages;
    api.listMessages.mockRejectedValueOnce(new Error("offline"));
    await recover();
    expect(store.getState().messages).toEqual(before);
    await recover();
    expect(store.getState().messages.map((m) => m.text)).toEqual(["task", "complete answer", "local"]);
  });
});
