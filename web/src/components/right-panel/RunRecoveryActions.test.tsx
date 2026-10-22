import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunRecoveryActions } from "./RunRecoveryActions";
import type { AgentRun } from "../../types/ipc";

const api = vi.hoisted(() => ({ resumeRun: vi.fn(), cancelRun: vi.fn() }));
vi.mock("../../ipc", () => ({ typedIPC: api }));

const run: AgentRun = {
  id: "r", session_id: "s", mode: "chat", status: "interrupted", title: "task",
  created_at: "t", started_at: "t",
  metadata: { workspace_snapshot: "/workspace", model_snapshot: {
    engine: "native", model: "test", provider_id: null, protocol: null,
    reasoning_effort: null, context_window: 10000,
  } },
};

describe("run recovery", () => {
  beforeEach(() => vi.resetAllMocks());

  it("requires review and retries an uncertain submission with the same key", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    api.resumeRun.mockRejectedValueOnce(new Error("connection lost")).mockResolvedValueOnce({ run, created: false });
    render(<RunRecoveryActions run={run} refresh={refresh} />);
    expect(screen.getByRole("button", { name: "继续此任务" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button"));
    await screen.findByText("connection lost");
    fireEvent.click(screen.getByRole("button", { name: "继续此任务" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(api.resumeRun).toHaveBeenCalledTimes(2);
    expect(api.resumeRun.mock.calls[0]).toEqual(api.resumeRun.mock.calls[1]);
    expect(api.resumeRun.mock.calls[0][2]).toBe(true);
    expect(screen.getByRole("button", { name: "已创建接续运行" })).toBeDisabled();
  });

  it("shows a cancellation request without claiming teardown finished", async () => {
    api.cancelRun.mockResolvedValue({ requested: true, run: { ...run, status: "running" } });
    render(<RunRecoveryActions run={{ ...run, status: "running" }} refresh={vi.fn().mockResolvedValue(undefined)} />);
    fireEvent.click(screen.getByRole("button", { name: "取消此任务" }));
    await screen.findByRole("button", { name: "正在取消…" });
    expect(screen.queryByText("已取消")).not.toBeInTheDocument();
  });

  it("keeps cancellation retryable on failure", async () => {
    api.cancelRun.mockRejectedValue(new Error("offline"));
    render(<RunRecoveryActions run={{ ...run, status: "queued" }} refresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "取消此任务" })).toBeEnabled();
  });

  it("does not offer a fabricated resume action for legacy records", () => {
    render(<RunRecoveryActions run={{ ...run, metadata: null }} refresh={vi.fn()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
