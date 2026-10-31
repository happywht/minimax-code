import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { AgentRun, RunWorkspaceDiffResult } from "../../../types/ipc";

const api = vi.hoisted(() => ({ getServiceIdentity: vi.fn(), listProjects: vi.fn(), getRunSteps: vi.fn(),
  gitRunWorkspaceDiff: vi.fn(), verifyProjectRun: vi.fn(), getRun: vi.fn(), confirm: vi.fn() }));
vi.mock("../../../ipc", () => ({ ipc: { agentBaseUrl: "host", isMock: false, onSideCar: () => () => {} }, typedIPC: api }));
vi.mock("../../../stores", () => ({ useSessionStore: { getState: () => ({ currentProjectId: "p", currentSessionId: "s" }) } }));
vi.mock("../../modals/ConfirmationDialog", () => ({ requestConfirmation: api.confirm }));
import { RunManualVerification } from "../RunManualVerification";

const run: AgentRun = { id: "source", session_id: "s", mode: "execute", status: "completed", title: "Task",
  created_at: "2026-01-01", metadata: { project_id: "p", workspace_snapshot: "/workspace" } };
const review: RunWorkspaceDiffResult = { run_id: "source", session_id: "s", project_id: "p", workspace: "/workspace",
  workspace_identity: "1:2", service_id: "service", scope: "current_workspace", diff: "diff", staged_diff: "", untracked: [] };

beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  api.getServiceIdentity.mockResolvedValue({ service_id: "service" });
  api.listProjects.mockResolvedValue({ projects: [{ id: "p", validate_command: "pytest -q", archived: false }] });
  api.getRunSteps.mockResolvedValue({ run, cursor: 3, steps: [] });
  api.gitRunWorkspaceDiff.mockResolvedValue(review);
  api.confirm.mockResolvedValue(true);
  api.verifyProjectRun.mockResolvedValue({ created: true, service_id: "service", project_id: "p", session_id: "s",
    run: { id: "verify", session_id: "s", status: "queued", metadata: { source_run_id: "source", manual_project_validation: true } } });
});

it("requires review and displays the independent verification result", async () => {
  api.getRun.mockResolvedValue({ run: { id: "verify", session_id: "s", status: "completed",
    metadata: { source_run_id: "source", project_id: "p", manual_project_validation: true } },
    result: { verification: { verdict: "failed", reason: "validation_failed" } } });
  render(<RunManualVerification run={run} review={review} />);
  expect(api.verifyProjectRun).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "审阅并验证当前工作区" }));
  await screen.findByText(/验证运行 verify/);
  expect(api.confirm).toHaveBeenCalledOnce();
  expect(api.verifyProjectRun).toHaveBeenCalledWith(expect.objectContaining({ source_run_id: "source", expected_cursor: 3,
    expected_command: "pytest -q", expected_workspace_identity: "1:2", confirmed: true }));
  fireEvent.click(screen.getByRole("button", { name: "读取验证结果" }));
  expect(await screen.findByText(/validation_failed/)).toBeInTheDocument();
});

it("retains an uncertain admission and does not automatically retry", async () => {
  api.verifyProjectRun.mockRejectedValue(new Error("connection lost"));
  render(<RunManualVerification run={run} review={review} />);
  fireEvent.click(screen.getByRole("button", { name: "审阅并验证当前工作区" }));
  await screen.findByText(/上次提交的结果未确认/);
  await waitFor(() => expect(screen.getByRole("button", { name: "审阅并验证当前工作区" })).toBeDisabled());
  expect(api.verifyProjectRun).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(1);
});
