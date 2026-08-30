/**
 * Tests for TeamRunPanel — empty state, run card rendering, the persisted
 * history section, and the teamRunStore spawn optimistic-entry flow.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TeamRunPanel } from "../src/components/right-panel/TeamRunPanel";
import { useTeamRunStore } from "../src/stores/teamRunStore";
import { typedIPC } from "../src/ipc/client";

// teamRunStore imports from "../ipc/client" directly while TeamRunPanel
// goes through the "../ipc" barrel — the barrel is a pure re-export, so
// mocking the underlying module covers both resolution paths.
// `ipc`/`typedIPC` are instances; spreading them would drop prototype
// methods, so patch in place with Object.assign instead.
vi.mock("../src/ipc/client", async () => {
  const actual =
    await vi.importActual<typeof import("../src/ipc/client")>("../src/ipc/client");
  return {
    ...actual,
    ipc: Object.assign(actual.ipc, {
      on: vi.fn(() => () => undefined),
    }),
    typedIPC: Object.assign(actual.typedIPC, {
      listRuns: vi.fn().mockResolvedValue({ runs: [] }),
      spawnTeam: vi.fn(),
    }),
  };
});

const RUN = {
  task_id: "task_1",
  team_id: "team_1",
  team_name: "调研小队",
  status: "agent_started" as const,
  progress: 0.5,
  agents_total: 2,
  agents_completed: 1,
  agent_name: "searcher",
  started_at: 1,
  updated_at: 2,
};

function historyRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "teamrun_abc",
    session_id: "s1",
    mode: "team" as const,
    status: "completed" as const,
    title: "调研 v2",
    created_at: "2026-08-01T10:00:00Z",
    ...overrides,
  };
}

describe("TeamRunPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useTeamRunStore.setState({ runs: [] });
    vi.mocked(typedIPC.listRuns).mockResolvedValue({ runs: [] });
  });

  it("shows the empty state when no runs exist", () => {
    useTeamRunStore.setState({ runs: [] });
    render(<TeamRunPanel />);
    expect(screen.getByTestId("team-run-panel-empty")).toHaveTextContent("暂无团队运行");
    expect(screen.queryByTestId("team-run-panel")).not.toBeInTheDocument();
  });

  it("renders active run cards with progress", () => {
    useTeamRunStore.setState({ runs: [RUN] });
    render(<TeamRunPanel />);
    expect(screen.getByTestId("team-run-panel")).toBeInTheDocument();
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("调研小队");
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("1/2 个 Agent");
  });

  it("removes a completed run via its dismiss button", () => {
    useTeamRunStore.setState({
      runs: [{ ...RUN, status: "completed" as const, result: undefined }],
    });
    render(<TeamRunPanel />);
    fireEvent.click(screen.getByRole("button", { name: "移除团队运行" }));
    expect(useTeamRunStore.getState().runs).toHaveLength(0);
  });

  const SANDBOX_SUMMARY = {
    collected: 2,
    merged_files: 3,
    conflicts: [],
    errors: 0,
    skipped_runs: [],
    skipped_files: [],
  };

  it("renders the sandbox badge and collect stats for a sandboxed run", () => {
    useTeamRunStore.setState({
      runs: [
        {
          ...RUN,
          status: "completed" as const,
          result: {
            merged_text: "汇总结果",
            success: true,
            conflicts: [],
            sandbox: true,
            sandbox_summary: SANDBOX_SUMMARY,
          },
        },
      ],
    });
    render(<TeamRunPanel />);
    expect(screen.getByTestId("teamrun-sandbox-task_1")).toHaveTextContent("沙箱");
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("沙盒已回收 2 个运行");
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("合并落盘 3 个文件");
    // Clean collect — no warning line.
    expect(screen.getByTestId("teamrun-task_1")).not.toHaveTextContent("未合并");
  });

  it("warns when sandbox runs were skipped or errored during collect", () => {
    useTeamRunStore.setState({
      runs: [
        {
          ...RUN,
          status: "completed" as const,
          result: {
            merged_text: "汇总结果",
            success: true,
            conflicts: [],
            sandbox: true,
            sandbox_summary: {
              ...SANDBOX_SUMMARY,
              collected: 1,
              skipped_runs: ["team_x_01_writer"],
              errors: 2,
            },
          },
        },
      ],
    });
    render(<TeamRunPanel />);
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("1 个沙盒运行未合并");
    expect(screen.getByTestId("teamrun-task_1")).toHaveTextContent("合并错误 2 个");
  });

  it("renders no sandbox rows when the run was not sandboxed", () => {
    useTeamRunStore.setState({
      runs: [
        {
          ...RUN,
          status: "completed" as const,
          result: { merged_text: "汇总结果", success: true, conflicts: [] },
        },
      ],
    });
    render(<TeamRunPanel />);
    expect(screen.queryByTestId("teamrun-sandbox-task_1")).not.toBeInTheDocument();
    expect(screen.getByTestId("teamrun-task_1")).not.toHaveTextContent("沙盒已回收");
  });

  it("renders persisted team-run history from run.list", async () => {
    vi.mocked(typedIPC.listRuns).mockResolvedValue({
      runs: [
        historyRun(),
        // Non-team runs are filtered out of the history section.
        historyRun({ id: "run_chat", mode: "chat" as const, title: "普通会话" }),
      ],
    });
    render(<TeamRunPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("teamrun-history")).toBeInTheDocument();
    });
    expect(screen.getByTestId("teamrun-history-teamrun_abc")).toHaveTextContent("调研 v2");
    expect(screen.queryByTestId("teamrun-history-run_chat")).not.toBeInTheDocument();
  });

  it("keeps the empty state (without history) when run.list has no team runs", async () => {
    vi.mocked(typedIPC.listRuns).mockResolvedValue({ runs: [] });
    render(<TeamRunPanel />);
    await waitFor(() => expect(typedIPC.listRuns).toHaveBeenCalled());
    expect(screen.queryByTestId("teamrun-history")).not.toBeInTheDocument();
  });
});

describe("teamRunStore.spawn", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useTeamRunStore.setState({ runs: [] });
  });

  it("inserts an optimistic pending entry and completes it with the result", async () => {
    vi.mocked(typedIPC.spawnTeam).mockResolvedValue({
      team_name: "调研小队",
      orchestration_mode: "parallel",
      merged_text: "汇总结果",
      sandbox: false,
      agents_run: [],
      conflicts: [],
      task_id: "teamrun_xyz",
      success: true,
    });

    await useTeamRunStore.getState().spawn({ team_name: "调研小队", request: "查一下" });

    const runs = useTeamRunStore.getState().runs;
    expect(runs).toHaveLength(1);
    expect(runs[0].task_id).toBe("teamrun_xyz");
    expect(runs[0].status).toBe("completed");
    expect(runs[0].result?.merged_text).toBe("汇总结果");
  });

  it("marks the pending entry failed when spawn rejects", async () => {
    vi.mocked(typedIPC.spawnTeam).mockRejectedValue(new Error("boom"));

    await useTeamRunStore.getState().spawn({ team_name: "调研小队", request: "查一下" });

    const runs = useTeamRunStore.getState().runs;
    expect(runs).toHaveLength(1);
    expect(runs[0].status).toBe("failed");
    expect(runs[0].task_id).toContain("pending-");
  });
});
