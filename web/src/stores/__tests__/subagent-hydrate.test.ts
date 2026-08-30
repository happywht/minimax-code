/**
 * useSubAgentStore.hydrate (R1 / evolution gap closure).
 *
 * v1.4.0 known limitation: the panel's state was event-only, so an
 * agent restart wiped every row even though the runs live on in
 * ``agent_runs`` (mode=subagent). hydrate() backfills the table via
 * ``run.list``; these tests pin the mapping and the "live events win"
 * merge rule. The real ``typed.ts`` bindings run against a spied
 * ``client.request`` (same harness as project-root-scope.test.ts).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const wire: Record<string, unknown> = {
  "run.list": { runs: [] },
};

vi.mock("../../ipc/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../ipc/client")>();

  let failListRuns = false;
  const spyClient = {
    request: vi.fn().mockImplementation((method: string, params?: Record<string, unknown>) => {
      if (method === "run.list") {
        if (failListRuns) return Promise.reject(new Error("db gone"));
        if (params?.mode !== "subagent") {
          return Promise.reject(new Error(`unexpected mode filter: ${String(params?.mode)}`));
        }
      }
      return Promise.resolve(wire[method] ?? {});
    }),
    start: vi.fn().mockResolvedValue(undefined),
    ping: vi.fn().mockResolvedValue(true),
    on: vi.fn(() => () => {}),
    close: vi.fn(),
  };

  return {
    ...mod,
    ipc: spyClient,
    typedIPC: mod.bindTypedIPC(spyClient as never),
    __setFailListRuns: (v: boolean) => {
      failListRuns = v;
    },
  };
});

import { ipc } from "../../ipc/client";
import { useSubAgentStore, runsForSession } from "../subAgent";
import type { AgentRun } from "../../types/ipc";

const requestSpy = (ipc as unknown as { request: ReturnType<typeof vi.fn> }).request;
const setFailListRuns = (v: boolean) =>
  (ipc as unknown as { __setFailListRuns?: (v: boolean) => void }).__setFailListRuns?.(v);

function subagentRow(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: "run_1",
    session_id: "sub_sess_1",
    mode: "subagent",
    status: "completed",
    title: "[subagent] coder",
    user_message_id: null,
    assistant_message_id: null,
    created_at: "2026-08-29T10:00:00Z",
    started_at: "2026-08-29T10:00:01Z",
    completed_at: "2026-08-29T10:02:00Z",
    error: null,
    metadata: {
      parent_session_id: "sess_A",
      agent_name: "coder",
      agent_id: "ag_1",
      prompt: "do the thing",
      source: "tool",
      sandbox: false,
      result_text: "did the thing",
      iterations: 3,
      stub: false,
      partial: false,
      reported: true,
    },
    ...overrides,
  };
}

beforeEach(() => {
  requestSpy.mockClear();
  wire["run.list"] = { runs: [] };
  setFailListRuns(false);
  useSubAgentStore.getState().reset();
});

describe("useSubAgentStore.hydrate — wire shape", () => {
  it("requests run.list with the subagent mode filter", async () => {
    await useSubAgentStore.getState().hydrate();
    const call = requestSpy.mock.calls.find(([m]) => m === "run.list");
    expect(call).toBeDefined();
    expect(call?.[1]).toMatchObject({ mode: "subagent" });
  });
});

describe("useSubAgentStore.hydrate — row mapping", () => {
  it("maps a completed row: terminal status, epoch timestamps, title prefix stripped", async () => {
    wire["run.list"] = { runs: [subagentRow()] };

    await useSubAgentStore.getState().hydrate();
    const run = useSubAgentStore.getState().runs.run_1;
    expect(run).toBeDefined();
    expect(run.status).toBe("completed");
    expect(run.progress).toBe(1);
    expect(run.summary).toBe("coder");
    expect(run.agent_name).toBe("coder");
    expect(run.agent_id).toBe("ag_1");
    expect(run.prompt).toBe("do the thing");
    expect(run.parent_session_id).toBe("sess_A");
    expect(run.text).toBe("did the thing");
    expect(run.error).toBeUndefined();
    expect(run.started_at).toBe(Date.parse("2026-08-29T10:00:01Z"));
    expect(run.updated_at).toBe(Date.parse("2026-08-29T10:02:00Z"));
    expect(run.finished_at).toBe(Date.parse("2026-08-29T10:02:00Z"));
  });

  it("maps a failed row: error passthrough", async () => {
    wire["run.list"] = {
      runs: [subagentRow({ status: "failed", error: "wall clock fired" })],
    };

    await useSubAgentStore.getState().hydrate();
    const run = useSubAgentStore.getState().runs.run_1;
    expect(run.status).toBe("failed");
    expect(run.error).toBe("wall clock fired");
    expect(run.finished_at).toBe(Date.parse("2026-08-29T10:02:00Z"));
  });

  it("maps non-terminal DB statuses to \"started\" (restart orphans render idle, not spinning)", async () => {
    wire["run.list"] = {
      runs: [
        subagentRow({ id: "run_r", status: "running", completed_at: null }),
        subagentRow({ id: "run_p", status: "planning", completed_at: null }),
        subagentRow({ id: "run_a", status: "awaiting_approval", completed_at: null }),
      ],
    };

    await useSubAgentStore.getState().hydrate();
    const runs = useSubAgentStore.getState().runs;
    expect(runs.run_r.status).toBe("started");
    expect(runs.run_p.status).toBe("started");
    expect(runs.run_a.status).toBe("started");
    expect(runs.run_r.finished_at).toBeUndefined();
  });

  it("tolerates rows with no metadata (agent_name falls back to the stripped title)", async () => {
    wire["run.list"] = {
      runs: [subagentRow({ metadata: null, started_at: null, completed_at: null })],
    };

    await useSubAgentStore.getState().hydrate();
    const run = useSubAgentStore.getState().runs.run_1;
    expect(run.agent_name).toBe("coder");
    expect(run.parent_session_id).toBeUndefined();
    expect(run.started_at).toBe(Date.parse("2026-08-29T10:00:00Z"));
    expect(run.updated_at).toBe(Date.parse("2026-08-29T10:00:00Z"));
  });
});

describe("useSubAgentStore.hydrate — merge rules", () => {
  it("never overwrites a row the live event stream already filled", async () => {
    useSubAgentStore.getState().register({
      run_id: "run_1",
      agent_id: "ag_1",
      agent_name: "live-coder",
      prompt: "live prompt",
      status: "thinking",
      progress: 0.4,
      summary: "live summary",
      started_at: 1,
      updated_at: 2,
    });
    wire["run.list"] = { runs: [subagentRow()] };

    await useSubAgentStore.getState().hydrate();
    const run = useSubAgentStore.getState().runs.run_1;
    expect(run.agent_name).toBe("live-coder");
    expect(run.status).toBe("thinking");
  });

  it("backfilled rows scope by metadata.parent_session_id (rows hang off sub-sessions)", async () => {
    wire["run.list"] = {
      runs: [
        subagentRow({ id: "run_a" }),
        subagentRow({
          id: "run_b",
          session_id: "sub_sess_2",
          metadata: { parent_session_id: "sess_B", agent_name: "coder" },
        }),
      ],
    };

    await useSubAgentStore.getState().hydrate();
    const state = useSubAgentStore.getState();
    expect(runsForSession(state, "sess_A").map((r) => r.run_id)).toEqual(["run_a"]);
    expect(runsForSession(state, "sess_B").map((r) => r.run_id)).toEqual(["run_b"]);
  });

  it("fails open: a rejected run.list leaves state and error untouched", async () => {
    useSubAgentStore.getState().register({
      run_id: "run_live",
      agent_id: "ag_live",
      agent_name: "live",
      prompt: "",
      status: "started",
      progress: 0,
      summary: "",
      started_at: 1,
      updated_at: 1,
    });
    setFailListRuns(true);

    await useSubAgentStore.getState().hydrate();
    const state = useSubAgentStore.getState();
    expect(Object.keys(state.runs)).toEqual(["run_live"]);
    expect(state.error).toBeNull();
  });
});
