import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc } from "../../ipc";
import { useSessionStore, useSubAgentStore } from "../../stores";
import { SubAgentPanel } from "./SubAgentPanel";

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "session", sessions: [{ id: "session", title: "Session",
    project_id: "project", engine: "native", archived: false, created_at: 0, updated_at: 0, model_id: null }] });
  useSubAgentStore.setState({ runs: { child: { run_id: "child", agent_id: "worker", agent_name: "Worker",
    parent_session_id: "session", status: "completed", progress: 1, summary: "done", prompt: "",
    started_at: 1, updated_at: 2 } } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); useSubAgentStore.getState().reset(); });

it("blocks conflicting collection and sends the reviewed revision on explicit collection", async () => {
  const review = { run_id: "child", session_id: "session", project_id: "project", service_id: "service",
    root: "/project", revision: "a".repeat(64), state: "completed", kind: "directory", omitted: [],
    files: [{ path: "a.txt", conflict: "parent_changed", diff: "difference" }] };
  const call = vi.spyOn(ipc, "request").mockResolvedValueOnce(review)
    .mockResolvedValueOnce({ ...review, revision: "b".repeat(64), files: [{ ...review.files[0], conflict: null }] })
    .mockResolvedValueOnce({ session_id: "session", project_id: "project", service_id: "service", report: { complete: true } });
  render(<SubAgentPanel sessionId="session" autoInit={false} />);
  fireEvent.click(screen.getByTestId("sub-agent-panel-row-child-header"));
  fireEvent.click(screen.getByRole("button", { name: "查看子任务工作区结果" }));
  expect(await screen.findByText(/冲突：parent_changed/)).toBeVisible();
  expect(screen.getByRole("button", { name: "收集已审阅的无冲突变化" })).toBeDisabled();
  expect(call).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "查看子任务工作区结果" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "收集已审阅的无冲突变化" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "收集已审阅的无冲突变化" }));
  await waitFor(() => expect(call).toHaveBeenLastCalledWith("run.child_workspace", {
    run_id: "child", session_id: "session", action: "collect", revision: "b".repeat(64), expected_service_id: "service",
  }));
  expect(await screen.findByText(/收集完成/)).toBeVisible();
});
