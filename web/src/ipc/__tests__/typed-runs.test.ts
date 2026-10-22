import { afterEach, describe, expect, it, vi } from "vitest";
import { IPCClient } from "../client";
import { mockRequest } from "../mock";
import { bindTypedIPC } from "../typed";
import type { RunAdmissionResult, RunGetResult } from "../../types/ipc";

describe("durable run contract", () => {
  afterEach(() => vi.useRealTimers());

  it("maps run control arguments without dropping the idempotency/review fields", async () => {
    const request = vi.fn().mockResolvedValue({});
    const api = bindTypedIPC({ request } as unknown as IPCClient);
    await api.startRun({ session_id: "s", content: "work", idempotency_key: "k" });
    await api.getRun("r");
    await api.cancelRun("r");
    await api.resumeRun("r", "resume-k", true);
    expect(request.mock.calls).toEqual([
      ["run.start", { session_id: "s", content: "work", idempotency_key: "k" }],
      ["run.get", { run_id: "r" }],
      ["run.cancel", { run_id: "r" }],
      ["run.resume", { run_id: "r", idempotency_key: "resume-k", reviewed: true }],
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
});
