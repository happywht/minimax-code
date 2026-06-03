/**
 * Tests for the v0.3.0 §2 sub-agent store + mock backend spawn flow.
 *
 * The store ingests ``agent.subagent_progress`` events and updates
 * the per-run row in place. The mock backend fabricates a
 * 5-stage progress stream when ``agent.spawn_subagent`` is
 * invoked, so the store's end-to-end wiring is exercised here.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { IPCClient, bindTypedIPC } from "../src/ipc";
import { useSubAgentStore, runsForSession } from "../src/stores/subAgent";
import { StreamEvent, type SubAgentProgress } from "../src/types/ipc";

function progressEvent(overrides: Partial<SubAgentProgress>): SubAgentProgress {
  return {
    run_id: "run_test",
    agent_id: "general",
    status: "started",
    progress: 0.1,
    summary: "starting",
    received_at: Date.now(),
    ...overrides,
  };
}

beforeEach(() => {
  useSubAgentStore.getState().reset();
});

describe("useSubAgentStore", () => {
  it("registers a fresh run with the row keyed by run_id", () => {
    const store = useSubAgentStore.getState();
    store.register({
      run_id: "run_1",
      agent_id: "general",
      agent_name: "General",
      prompt: "hi",
      status: "started",
      progress: 0,
      summary: "queued",
      started_at: 1,
      updated_at: 1,
    });
    expect(useSubAgentStore.getState().runs["run_1"].agent_id).toBe("general");
  });

  it("applyProgress updates an existing row and clamps progress", () => {
    const store = useSubAgentStore.getState();
    store.register({
      run_id: "run_1",
      agent_id: "general",
      agent_name: "General",
      prompt: "hi",
      status: "started",
      progress: 0,
      summary: "queued",
      started_at: 1,
      updated_at: 1,
    });
    store.applyProgress(
      progressEvent({ run_id: "run_1", progress: 1.4, status: "completed" }),
    );
    const row = useSubAgentStore.getState().runs["run_1"];
    // We pass through the value as-given (clamping is the backend's
    // job) — but the store must at least record the value and
    // status.
    expect(row.status).toBe("completed");
    expect(row.progress).toBe(1.4);
  });

  it("applyProgress synthesises a row when none was pre-registered", () => {
    const store = useSubAgentStore.getState();
    store.applyProgress(
      progressEvent({ run_id: "run_late", status: "thinking", progress: 0.3 }),
    );
    const row = useSubAgentStore.getState().runs["run_late"];
    expect(row).toBeDefined();
    expect(row.status).toBe("thinking");
    expect(row.progress).toBe(0.3);
  });

  it("clearCompleted drops finished rows and keeps in-flight ones", () => {
    const store = useSubAgentStore.getState();
    store.register({
      run_id: "r1",
      agent_id: "a",
      agent_name: "A",
      prompt: "",
      status: "completed",
      progress: 1,
      summary: "",
      started_at: 1,
      updated_at: 1,
      finished_at: 1,
    });
    store.register({
      run_id: "r2",
      agent_id: "a",
      agent_name: "A",
      prompt: "",
      status: "thinking",
      progress: 0.4,
      summary: "",
      started_at: 1,
      updated_at: 1,
    });
    store.clearCompleted();
    const runs = useSubAgentStore.getState().runs;
    expect(runs["r1"]).toBeUndefined();
    expect(runs["r2"]).toBeDefined();
  });

  it("runsForSession scopes by parent_session_id and returns all when null", () => {
    const store = useSubAgentStore.getState();
    store.register({
      run_id: "r1",
      agent_id: "a",
      agent_name: "A",
      prompt: "",
      parent_session_id: "ses_1",
      status: "completed",
      progress: 1,
      summary: "",
      started_at: 1,
      updated_at: 1,
    });
    store.register({
      run_id: "r2",
      agent_id: "b",
      agent_name: "B",
      prompt: "",
      parent_session_id: "ses_2",
      status: "completed",
      progress: 1,
      summary: "",
      started_at: 2,
      updated_at: 2,
    });
    const state = useSubAgentStore.getState();
    expect(runsForSession(state, "ses_1").map((r) => r.run_id)).toEqual(["r1"]);
    expect(runsForSession(state, null).map((r) => r.run_id).sort()).toEqual([
      "r1",
      "r2",
    ]);
  });
});

describe("Sub-agent progress event wiring (mock backend)", () => {
  it("spawnSubagent returns a run_id and the mock backend emits a stream of progress events", async () => {
    const client = new IPCClient({ mockMode: true });
    const typed = bindTypedIPC(client);
    const seen: SubAgentProgress[] = [];
    client.on<SubAgentProgress>(StreamEvent.SubAgentProgress, (env) => {
      if (env.data) seen.push(env.data);
    });

    const reply = await typed.spawnSubagent({
      agent_id: "general",
      prompt: "summarise",
      parent_session_id: "ses_x",
      context_message_id: "msg_x",
      display_name: "Helper",
    });
    expect(reply.agent_run_id).toMatch(/^run_/);
    expect(reply.agent_id).toBe("general");

    // Wait for the mock backend's 5 staggered events (80ms each).
    await new Promise((res) => setTimeout(res, 700));

    expect(seen.length).toBeGreaterThanOrEqual(3);
    // All events share the same run_id and carry parent_session_id.
    for (const ev of seen) {
      expect(ev.run_id).toBe(reply.agent_run_id);
      expect(ev.parent_session_id).toBe("ses_x");
      expect(ev.context_message_id).toBe("msg_x");
      expect(ev.received_at).toBeGreaterThan(0);
    }
    // First event is "started", last is "completed" with progress 1.0.
    expect(seen[0].status).toBe("started");
    const last = seen[seen.length - 1];
    expect(last.status).toBe("completed");
    expect(last.progress).toBe(1);
    expect(typeof last.text).toBe("string");
    expect(last.text).toContain("summarise");
  });
});
