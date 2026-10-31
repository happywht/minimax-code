import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { usePermissionStore, useRunTimelineStore, useSessionStore } from "../../stores";
import { CurrentRunOverview } from "./CurrentRunOverview";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps failure and unknown budget visible while full run details open separately", async () => {
  const run = { id: "r1", session_id: "s1", status: "failed", title: "task", error: "真实错误：连接失败",
    mode: "execute", metadata: { project_id: "p1" }, steps: [] };
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1", sessions: [{ id: "s1", project_id: "p1" }] as never });
  useRunTimelineStore.setState({ runs: { r1: run } as never, order: ["r1"], loading: false, error: null });
  vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue();
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(typedIPC, "getBudget").mockResolvedValue({ budget: { known_tokens: 0, reserved_tokens: 0,
    limit_tokens: null, remaining_estimate: null, unknown_attempts: 1, usage_complete: false } } as never);
  render(<CurrentRunOverview connection="connected" onOpenTimeline={vi.fn()} chatError="真实错误：连接失败" />);
  expect(screen.getByText("执行失败")).toBeVisible();
  expect(screen.getAllByText("真实错误：连接失败")).toHaveLength(1);
  expect(screen.getByText("真实错误：连接失败")).toBeVisible();
  await waitFor(() => expect(screen.getByText("1 项预算责任未知")).toBeVisible());
  expect(screen.queryByRole("dialog", { name: "任务概览" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
  expect(screen.getByRole("dialog", { name: "任务概览" })).toHaveTextContent("运行错误：真实错误：连接失败");
});


it("keeps a live approval request actionable in the compact status", async () => {
  const run = { id: "r2", session_id: "s2", status: "awaiting_approval", title: "task", mode: "execute", metadata: { project_id: "p1" }, steps: [] };
  useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p1", sessions: [{ id: "s2", project_id: "p1" }] as never });
  useRunTimelineStore.setState({ runs: { r2: run } as never, order: ["r2"], loading: false, error: null });
  usePermissionStore.setState({ pending: { req: { request_id: "req", session_id: "s2", run_id: "r2", tool: "shell" } } as never, resolving: {} });
  vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue();
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(typedIPC, "getBudget").mockResolvedValue({ budget: null } as never);
  const listener = vi.fn(); window.addEventListener("minimax:open-run-permission", listener);
  try {
    render(<CurrentRunOverview connection="connected" onOpenTimeline={vi.fn()} />);
    expect(screen.getByText("等待授权")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看授权请求" }));
    expect(listener).toHaveBeenCalledOnce();
    await act(async () => { await Promise.resolve(); });
  } finally { cleanup(); window.removeEventListener("minimax:open-run-permission", listener); usePermissionStore.setState({ pending: {} }); }
});
