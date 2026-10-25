import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunBudgetPanel } from "./RunBudgetPanel";
import type { AgentRun, BudgetStateResult } from "../../types/ipc";

const api = vi.hoisted(() => ({ getBudget: vi.fn(), reconcileBudget: vi.fn(), getRunSteps: vi.fn(), setCurrent: vi.fn(), loadForSession: vi.fn(), updateTimeline: vi.fn() }));
vi.mock("../../ipc", () => ({ typedIPC: api }));
vi.mock("../../stores", () => ({useSessionStore: {getState: () => ({setCurrent: api.setCurrent, currentSessionId: "owner-session"})},
  useRunTimelineStore: {getState: () => ({loadForSession: api.loadForSession}), setState: api.updateTimeline}}));
const run = (id: string): AgentRun => ({ id, session_id: "s", mode: "chat", status: "budget_paused", title: id, created_at: "t" });
const state = (id: string, reactive = false): BudgetStateResult => {
  const day = {day: "2026-10-26", known_tokens: 12, reserved_tokens: 88, unknown_attempts: 1,
    baseline_incomplete: false, historical_lower_bound: 0, historical_liability: null};
  return {coverage_started_at: "t", day, attempts: [{id: `attempt-${id}`, state: "unknown", estimated_tokens: 100,
    known_tokens: null, liability_tokens: null, reason: "EOF"}], budget: {family_id: id, limit_tokens: 1000,
    known_tokens: 12, reserved_tokens: 88, unknown_attempts: 1, remaining_estimate: 900,
    enforcement: reactive ? "reactive" : "estimated", pause_reason: "unresolved_attempt",
    baseline_incomplete: false, usage_complete: false, daily_limit: 2000, day,
    assumptions: "estimate", price: null, currency: null}};
};

describe("durable budget", () => {
  beforeEach(() => vi.resetAllMocks());
  it("keeps unknown usage distinct from zero and authorizes liability explicitly", async () => {
    api.getBudget.mockResolvedValue(state("A"));
    api.reconcileBudget.mockResolvedValue({ok: true});
    render(<RunBudgetPanel run={run("A")} />);
    await screen.findByText(/任务累计已知至少 12/);
    expect(screen.getByText(/Native · 请求发送前估算预留/)).toBeInTheDocument();
    expect(screen.getByText(/实际用量可能超过估算/)).toBeInTheDocument();
    const button = screen.getByRole("button", {name: /核定请求/});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("核定 token 责任"), {target: {value: "100"}});
    fireEvent.change(screen.getByLabelText("预算核对依据"), {target: {value: "checked offline evidence"}});
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);
    await waitFor(() => expect(api.reconcileBudget).toHaveBeenCalledWith({target: "attempt", target_id: "attempt-A",
      liability_tokens: 100, reason: "checked offline evidence", acknowledged: true}));
  });
  it("ignores a delayed previous run response and clears its authorization form", async () => {
    let resolveA!: (value: BudgetStateResult) => void;
    api.getBudget.mockImplementation((id: string) => id === "A" ? new Promise((resolve) => { resolveA = resolve; }) : Promise.resolve(state("B", true)));
    const view = render(<RunBudgetPanel run={run("A")} />);
    view.rerender(<RunBudgetPanel run={run("B")} />);
    await screen.findByText(/Codex · 用量通知驱动中断/);
    await act(async () => resolveA(state("A")));
    expect(screen.getByText(/Codex · 用量通知驱动中断/)).toBeInTheDocument();
    expect(screen.queryByText(/Native ·/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("核定 token 责任"), {target: {value: "100"}});
    fireEvent.change(screen.getByLabelText("预算核对依据"), {target: {value: "B evidence"}});
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", {name: /核定请求/}));
    await waitFor(() => expect(api.reconcileBudget).toHaveBeenCalledWith(expect.objectContaining({target_id: "attempt-B"})));
    view.rerender(<RunBudgetPanel run={run("C")} />);
    await screen.findByLabelText("核定 token 责任");
    expect(screen.getByLabelText("核定 token 责任")).toHaveValue(null);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });
  it("surfaces missing state as unknown without enabling reconciliation", async () => {
    api.getBudget.mockRejectedValue(new Error("storage offline"));
    render(<RunBudgetPanel run={run("A")} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("预算状态未知：storage offline");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
  it("links a daily blocker from another family to its owning task", async () => {
    api.getBudget.mockResolvedValue({...state("clean"), day_blockers: {total: 1, items: [{attempt_id: "unknown-child", family_id: "other",
      run_id: "owner-run", session_id: "owner-session", known_tokens: null}]}});
    api.loadForSession.mockResolvedValue(undefined);
    api.getRunSteps.mockResolvedValue({run: run("owner-run"), steps: []});
    render(<RunBudgetPanel run={run("clean")} />);
    fireEvent.click(await screen.findByRole("button", {name: "查看责任任务 owner-run"}));
    await waitFor(() => expect(api.getRunSteps).toHaveBeenCalledWith("owner-run"));
    expect(api.setCurrent).toHaveBeenCalledWith("owner-session");
    expect(api.reconcileBudget).not.toHaveBeenCalled();
  });

  it.each([
    [true, true, "当日用量证据完整（截至当前快照）", false],
    [false, true, "当日用量证据未完整", true],
    [false, false, "当日用量证据未完整", true],
    [undefined, undefined, "当日用量完整性未知", true],
  ])("separates whole-day measurement from resolved liability: %s/%s", async (measured, resolved, label, lowerBound) => {
    const response = state("day");
    response.day = {...response.day, known_tokens:0, reserved_tokens:0, unknown_attempts:0,
      usage_complete:measured, accounting_resolved:resolved};
    response.budget!.day = response.day;
    api.getBudget.mockResolvedValue(response);
    render(<RunBudgetPanel run={run("day")} />);
    const status = await screen.findByTestId("daily-budget-completeness");
    expect(status).toHaveTextContent(label);
    expect(screen.getByText(/本机 2026-10-26/)).toHaveTextContent(lowerBound ? "已知至少 0" : "已知 0");
    if (resolved === true) expect(status).toHaveTextContent("无待核对的未知责任；活动请求可能尚未结束");
    else expect(status).not.toHaveTextContent("无待核对的未知责任");
    expect(api.reconcileBudget).not.toHaveBeenCalled();
  });
});
