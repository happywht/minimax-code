import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunWorkspaceDiffResult } from "../../types/ipc";
import { RunWorkspaceDiff } from "./RunWorkspaceDiff";

const run: AgentRun = { id: "run1", session_id: "s1", mode: "execute", status: "completed", title: "Task",
  created_at: "now", metadata: { workspace_snapshot: "/work/tree", project_id: "p1" } };
const result: RunWorkspaceDiffResult = { run_id: "run1", session_id: "s1", project_id: "p1", workspace: "/work/tree",
  scope: "current_workspace", diff: "unstaged content", staged_diff: "staged content", untracked: ["new.txt"] };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1" });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the scoped current workspace without attributing the diff to the task", async () => {
  const call = vi.spyOn(typedIPC, "gitRunWorkspaceDiff").mockResolvedValue(result);
  render(<RunWorkspaceDiff run={run} />);
  fireEvent.click(screen.getByRole("button", { name: "查看工作区当前差异" }));
  await screen.findByText(/这是当前共享工作区的状态/);
  expect(call).toHaveBeenCalledWith("run1", "s1");
  expect(screen.getByLabelText("未暂存差异")).toHaveTextContent("unstaged content");
  expect(screen.getByLabelText("已暂存差异")).toHaveTextContent("staged content");
  expect(screen.getByLabelText("未跟踪文件")).toHaveTextContent("new.txt");
  expect(screen.getByText(/不能归因于此任务/)).toBeVisible();
});

it("discards a late response after a session or project switch", async () => {
  const pending = deferred<RunWorkspaceDiffResult>();
  vi.spyOn(typedIPC, "gitRunWorkspaceDiff").mockReturnValue(pending.promise);
  render(<RunWorkspaceDiff run={run} />);
  fireEvent.click(screen.getByRole("button", { name: "查看工作区当前差异" }));
  act(() => useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p2" }));
  await act(async () => { pending.resolve(result); await pending.promise; });
  expect(screen.queryByLabelText("未暂存差异")).toBeNull();
  expect(screen.getByRole("button", { name: "查看工作区当前差异" })).toBeDisabled();
});

it("refuses mismatched responses instead of showing another workspace", async () => {
  vi.spyOn(typedIPC, "gitRunWorkspaceDiff").mockResolvedValue({ ...result, workspace: "/other" });
  render(<RunWorkspaceDiff run={run} />);
  fireEvent.click(screen.getByRole("button", { name: "查看工作区当前差异" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("关联不匹配");
  expect(screen.queryByLabelText("未暂存差异")).toBeNull();
});
