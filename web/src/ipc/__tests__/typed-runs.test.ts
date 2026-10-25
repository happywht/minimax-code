import { afterEach, describe, expect, it, vi } from "vitest";
import { IPCClient } from "../client";
import { mockRequest } from "../mock";
import { bindTypedIPC } from "../typed";
import { mockRuns } from "../mockData";
import type { RunAdmissionResult, RunGetResult, ListMessagesResult } from "../../types/ipc";

describe("durable run contract", () => {
  afterEach(() => vi.useRealTimers());

  it("maps run control arguments without dropping the idempotency/review fields", async () => {
    const request = vi.fn().mockResolvedValue({});
    const api = bindTypedIPC({ request } as unknown as IPCClient);
    await api.startRun({ session_id: "s", content: "work", idempotency_key: "k" });
    await api.getRun("r");
    await api.cancelRun("r");
    await api.resumeRun("r", "resume-k", true);
    await api.continueRun("s", "original-run");
    expect(request.mock.calls).toEqual([
      ["run.start", { session_id: "s", content: "work", idempotency_key: "k" }],
      ["run.get", { run_id: "r" }],
      ["run.cancel", { run_id: "r" }],
      ["run.resume", { run_id: "r", idempotency_key: "resume-k", reviewed: true }],
      ["agent.continue_run", { session_id: "s", run_id: "original-run" }],
    ]);
  });

  it("mock admission deduplicates, rejects conflicting input and persists cancellation", async () => {
    vi.useFakeTimers();
    const client = new IPCClient();
    const params = { session_id: "mock-admission-test", content: "write once", idempotency_key: "one" };
    const first = await mockRequest<RunAdmissionResult>("run.start", params, client);
    const retry = await mockRequest<RunAdmissionResult>("run.start", params, client);
    expect(retry.created).toBe(false);
    expect(retry.run.id).toBe(first.run.id);
    await expect(mockRequest("run.start", { ...params, content: "different" }, client)).rejects.toThrow("different input");
    await mockRequest("run.cancel", { run_id: first.run.id }, client);
    await vi.runAllTimersAsync();
    const final = await mockRequest<RunGetResult>("run.get", { run_id: first.run.id }, client);
    expect(final.run.status).toBe("cancelled");
    expect(final.result).toMatchObject({ run_id: first.run.id, cancelled: true });
    await expect(mockRequest("run.resume", { run_id: first.run.id, idempotency_key: "again" }, client)).rejects.toThrow("Review");
    await expect(mockRequest("run.resume", { run_id: first.run.id, idempotency_key: "one", reviewed: true }, client)).rejects.toThrow("different input");
    const resumed = await mockRequest<RunAdmissionResult>("run.resume", { run_id: first.run.id, idempotency_key: "again", reviewed: true }, client);
    expect(resumed.run.resumed_from_id).toBe(first.run.id);
    await vi.runAllTimersAsync();
  });

  it("mock completed runs expose the same message IDs and text through history recovery", async () => {
    vi.useFakeTimers();
    const client = new IPCClient();
    const params = { session_id: "mock-history-test", content: "remember this", idempotency_key: "history-once" };
    const admitted = await mockRequest<RunAdmissionResult>("run.start", params, client);
    await vi.runAllTimersAsync();
    const { run, result } = await mockRequest<RunGetResult>("run.get", { run_id: admitted.run.id }, client);
    if (!result || "error" in result) throw new Error("Expected a completed mock receipt");
    expect(run.request_key).toBe(params.idempotency_key);
    const history = await mockRequest<ListMessagesResult>("message.list", { session_id: params.session_id }, client);
    expect(history.messages.map((message) => message.id)).toEqual([run.user_message_id, run.assistant_message_id]);
    expect(history.messages[1]).toMatchObject({ id: result?.message_id, text: result?.text, metadata: { run_id: run.id } });
    await mockRequest("run.start", params, client);
    expect(await mockRequest("message.list", { session_id: params.session_id }, client)).toEqual(history);
    expect(await mockRequest("message.list", { session_id: "other-mock-session" }, client)).toEqual({ messages: [] });
    await mockRequest("message.update", { message_id: run.user_message_id, content: "edited" }, client);
    await mockRequest("message.delete", { message_id: run.assistant_message_id }, client);
    const edited = await mockRequest<ListMessagesResult>("message.list", { session_id: params.session_id }, client);
    expect(edited.messages.map((message) => message.text)).toEqual(["edited"]);
  });

  it("mock continuation pins its source and retries the same run", async () => {
    vi.useFakeTimers();
    const client = new IPCClient();
    const source = { id: "mock-truncated-source", session_id: "mock-continuation", mode: "plan" as const,
      status: "completed" as const, title: "truncated", created_at: "2026-10-23T00:00:00Z",
      metadata: { truncated: true } };
    mockRuns.set(source.id, { run: source, steps: [] });
    const params = { session_id: source.session_id, run_id: source.id };
    const first = await mockRequest<import("../../types/ipc").SendMessageResult>("agent.continue_run", params, client);
    const retry = await mockRequest<import("../../types/ipc").SendMessageResult>("agent.continue_run", params, client);
    expect(retry.run_id).toBe(first.run_id);
    await vi.runAllTimersAsync();
    const final = await mockRequest<RunGetResult>("run.get", { run_id: first.run_id }, client);
    expect(final.run).toMatchObject({ mode: "plan", resumed_from_id: source.id, status: "completed" });
    expect(await mockRequest("agent.continue_run", params, client)).toEqual(final.result);
    expect(await mockRequest("agent.continue_run", { ...params, session_id: "other" }, client))
      .toMatchObject({ ok: false });
  });
});
