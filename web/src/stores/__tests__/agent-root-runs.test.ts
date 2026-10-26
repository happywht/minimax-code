import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentRun, SpawnSubagentParams } from "../../types/ipc";
const api = vi.hoisted(() => ({ admitSubagent: vi.fn(), getRun: vi.fn(), cancelSubagent: vi.fn(), getRunSteps: vi.fn() }));
vi.mock("../../ipc", () => ({ typedIPC: api, ipc: { start: vi.fn(), on: vi.fn(() => () => undefined), onSideCar: vi.fn(() => () => undefined) } }));
import { useSubAgentStore as store } from "../subAgent";
import { useSessionStore } from "../sessionStore";

const args: SpawnSubagentParams = { agent_id: "agent", agent_name: "Worker", prompt: "Inspect", session_id: "s1", idempotency_key: "stable" };
const run = (id = "r1", status: AgentRun["status"] = "running"): AgentRun => ({ id, session_id: `exec-${id}`, mode: "execute", status, title: "Worker", created_at: "2026-10-26T00:00:00Z" });
const snapshot = (row = run(), partial = false) => ({ run: row, result: { run_id: row.id, session_id: row.session_id,
  text: `answer-${row.id}`, run_status: row.status, partial, cancelled: false } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { resolve, promise }; }
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); store.getState().reset();
  useSessionStore.setState({ currentSessionId: "s1", sessions: ["s1", "s2"].map((id) => ({ id, title: id, engine: "native", archived: false, created_at: 0, updated_at: 0, model_id: null })) });
  api.admitSubagent.mockResolvedValue({ run: run(), created: true }); api.getRun.mockResolvedValue(snapshot());
});

describe("durable public agent roots", () => {
  it("saves intent before send and reloads the same run after a lost ACK", async () => {
    api.admitSubagent.mockImplementationOnce(() => {
      expect(localStorage.getItem("minimax-code:agent-roots:v1")).toContain("stable");
      throw new Error("lost ACK");
    });
    await store.getState().spawn(args);
    const pending = Object.values(store.getState().runs)[0];
    expect(pending.admission_unknown).toBe(true);
    store.getState().reset(); await store.getState().init();
    await store.getState().retry(pending.run_id);
    expect(api.admitSubagent.mock.calls.map((call) => call[0])).toEqual([args, args]);
    expect(Object.keys(store.getState().runs)).toEqual(["r1"]);
    store.getState().reset(); await store.getState().init();
    expect(store.getState().runs.r1.session_id).toBe("exec-r1");
  });
  it("does not downgrade a confirmed same-key retry after an older request fails", async () => {
    let reject!: (reason: Error) => void;
    api.admitSubagent.mockReturnValueOnce(new Promise((_yes, no) => { reject = no; }));
    const first = store.getState().spawn(args);
    await store.getState().spawn(args);
    reject(new Error("old lost ACK")); await first;
    expect(store.getState().runs.r1.admission_unknown).toBe(false);
    expect(Object.keys(store.getState().runs)).toEqual(["r1"]);
  });
  it("uses per-row CAS against delayed same-session refresh results", async () => {
    await store.getState().spawn(args);
    const delayed = deferred<ReturnType<typeof snapshot>>();
    api.getRun.mockReturnValueOnce(delayed.promise);
    const old = store.getState().refresh();
    api.getRun.mockResolvedValue(snapshot(run("r1", "completed")));
    await store.getState().refresh();
    const current = structuredClone(store.getState().runs.r1);
    delayed.resolve(snapshot(run("r1", "running"))); await old;
    expect(store.getState().runs.r1).toEqual(current);
    expect(store.getState().runs.r1.run_status).toBe("completed");
  });
  it("does not replace a completed receipt with an older successful admission ACK", async () => {
    const delayed = deferred<{run: AgentRun; created: boolean}>();
    api.admitSubagent.mockReturnValueOnce(delayed.promise);
    const first = store.getState().spawn(args);
    api.getRun.mockResolvedValue(snapshot(run("r1", "completed")));
    await store.getState().spawn(args);
    api.getRun.mockRejectedValue(new Error("refresh offline"));
    delayed.resolve({run:run("r1","queued"),created:true}); await first;
    expect(store.getState().runs.r1.run_status).toBe("completed");
    expect(store.getState().runs.r1.text).toBe("answer-r1");
  });
  it("keeps active ownership on refresh failure and does not invent cancelled status", async () => {
    await store.getState().spawn(args);
    api.getRun.mockRejectedValue(new Error("offline")); api.cancelSubagent.mockResolvedValue({ ok: true, cancelled: true });
    await store.getState().cancelRun("r1");
    expect(store.getState().runs.r1.run_status).toBe("running");
    expect(store.getState().runs.r1.cancel_requested).toBe(true);
    expect(store.getState().runs.r1.status).toBe("thinking");
    api.getRun.mockResolvedValue(snapshot(run("r1", "cancelled")));
    await store.getState().refresh();
    expect(store.getState().runs.r1.status).toBe("cancelled");
  });
  it.each([["completed", true], ["budget_paused", false], ["interrupted", false]] as const)("preserves truthful %s receipt", async (status, partial) => {
    api.getRun.mockResolvedValue(snapshot(run("r1", status), partial));
    await store.getState().spawn(args);
    expect(store.getState().runs.r1.run_status).toBe(status);
    expect(store.getState().runs.r1.partial).toBe(partial);
    expect(store.getState().runs.r1.status).toBe("failed");
  });
  it("does not open a delayed run after the source session changes", async () => {
    await store.getState().spawn(args);
    const delayed = deferred<{ run: AgentRun; steps: never[]; cursor: number }>();
    api.getRunSteps.mockReturnValueOnce(delayed.promise);
    const opening = store.getState().openRun("r1");
    useSessionStore.setState({ currentSessionId: "s2" });
    delayed.resolve({ run: run(), steps: [], cursor: 0 });
    expect(await opening).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBe("s2");
  });
  it("refuses transport when the stable key cannot be persisted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new Error("storage unavailable"); });
    await expect(store.getState().spawn(args)).rejects.toThrow("storage unavailable");
    expect(api.admitSubagent).not.toHaveBeenCalled();
  });
});
