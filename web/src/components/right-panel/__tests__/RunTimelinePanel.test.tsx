import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RunTimelinePanel } from "../RunTimelinePanel";
import { usePermissionStore, useRunTimelineStore, useSessionStore } from "../../../stores";
import type { AgentRun, AgentRunStep } from "../../../types/ipc";

function makeRun(): AgentRun {
  return {
    id: "run-1234567890",
    session_id: "session-1",
    mode: "chat",
    status: "failed",
    title: "Debug run",
    created_at: "2026-07-12T00:00:00Z",
  };
}

function makeStep(overrides: Partial<AgentRunStep>): AgentRunStep {
  return {
    id: "step-1",
    run_id: "run-1234567890",
    session_id: "session-1",
    kind: "observation",
    status: "completed",
    title: "Tool output",
    summary: "short output",
    started_at: "2026-07-12T00:00:00Z",
    ordinal: 1,
    ...overrides,
  };
}

function renderWithStep(step: AgentRunStep): void {
  const run = makeRun();
  useRunTimelineStore.setState({
    runs: { [run.id]: { ...run, steps: [step] } },
    order: [run.id],
    initialized: true,
    loading: false,
    error: null,
  });
  render(<RunTimelinePanel />);
}

describe("RunTimelinePanel", () => {
  beforeEach(() => {
    cleanup();
    useSessionStore.setState({ currentSessionId: null });
    usePermissionStore.setState({ pending: {}, resolving: {} });
    useRunTimelineStore.setState({
      runs: {},
      order: [],
      initialized: true,
      loading: false,
      error: null,
      loadForSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("keeps long step details compact but lets the user expand the full content", () => {
    const longSummary = `${"log line ".repeat(80)}UNIQUE_TAIL`;
    renderWithStep(makeStep({ summary: longSummary }));

    expect(screen.queryByText(/UNIQUE_TAIL/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开全部" }));

    expect(screen.getByText(/UNIQUE_TAIL/)).toBeInTheDocument();
    expect(screen.getByTestId("run-timeline-step-detail")).toHaveClass("overflow-auto");
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
  });

  it("shows the saved terminal tail and its truncation state", () => {
    renderWithStep(makeStep({ tool_name: "terminal", payload: { source: "terminal", command: "test", cwd: "/work", exit_code: 0, output_tail: "  saved tail\n", output_truncated: true } }));
    expect(screen.getByTestId("terminal-output-tail").textContent).toBe("  saved tail\n");
    expect(screen.getByText(/输出已截断/)).toBeInTheDocument();
    expect(screen.getByText(/退出码：0/)).toBeInTheDocument();
  });

  it("distinguishes missing output from an empty saved tail and unknown truncation", () => {
    renderWithStep(makeStep({ tool_name: "terminal", payload: { source: "terminal" } }));
    expect(screen.getByText(/没有已保存的输出/)).toBeInTheDocument();
    cleanup();
    renderWithStep(makeStep({ tool_name: "terminal", payload: { source: "terminal", output_tail: "" } }));
    expect(screen.getByText(/保存的输出为空/)).toBeInTheDocument();
    expect(screen.getByText(/截断情况未知/)).toBeInTheDocument();
  });

  it("warns that output after a checkpoint may be missing even when the saved tail was not truncated", () => {
    renderWithStep(makeStep({ tool_name: "terminal", payload: { source: "terminal", output_tail: "partial", output_truncated: false, output_may_be_incomplete: true } }));
    expect(screen.getByText(/记录时未截断/)).toBeInTheDocument();
    expect(screen.getByText(/检查点之后的输出可能丢失或不完整/)).toBeInTheDocument();
  });

  it("warns that an interrupted command may have unconfirmed side effects", () => {
    const run = { ...makeRun(), status: "interrupted" as const };
    const step = makeStep({ tool_name: "terminal", payload: { source: "terminal", command: "test", cwd: "/work" } });
    useRunTimelineStore.setState({ runs: { [run.id]: { ...run, steps: [step] } }, order: [run.id] });
    render(<RunTimelinePanel />);
    expect(screen.getByText(/副作用未知/)).toBeInTheDocument();
    expect(screen.getByText(/没有已保存的输出/)).toBeInTheDocument();
    expect(screen.getByText(/退出码：未知/)).toBeInTheDocument();
  });

  it("shows failed step errors in addition to the step summary", () => {
    renderWithStep(
      makeStep({
        status: "failed",
        summary: "Command failed",
        error: "exit code 1: missing dependency",
      }),
    );

    expect(screen.getByText(/Command failed/)).toBeInTheDocument();
    expect(screen.getByText(/错误：exit code 1: missing dependency/)).toBeInTheDocument();
  });

  it("shows the selected run's real validation command, exit and bounded output", () => {
    const run = { ...makeRun(), status: "completed" as const, metadata: { verification: { version: 1,
      verdict: "failed", reason: "validation_failed", requires_review: true,
      validation: { command: "pytest -q", ok: false, exit_code: 1, stdout: "failed test", stderr: "trace", stderr_truncated: true } } } };
    useRunTimelineStore.setState({ runs: { [run.id]: { ...run, steps: [] } }, order: [run.id] });
    render(<RunTimelinePanel />);
    const article = screen.getByText("Debug run").closest("article")!;
    expect(article).toHaveTextContent("校验未通过");
    expect(article).toHaveTextContent("pytest -q");
    expect(article).toHaveTextContent("退出码：1");
    expect(article).toHaveTextContent("failed test");
    expect(article).toHaveTextContent("标准错误（已截断）");
  });
});

describe("RunTimelinePanel auto-scroll", () => {
  const originalScrollTo = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "scrollTo",
  );
  let scrollToSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    cleanup();
    scrollToSpy = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      value: scrollToSpy,
      configurable: true,
    });
    useSessionStore.setState({ currentSessionId: "session-1" });
    usePermissionStore.setState({ pending: {}, resolving: {} });
    useRunTimelineStore.setState({
      runs: {},
      order: [],
      initialized: true,
      loading: false,
      error: null,
      // Network-free stub: a real loadForSession would probe a live agent
      // on 8765 and flip the IPC transport out of mock mode.
      loadForSession: async () => {},
    });
  });

  afterEach(() => {
    if (originalScrollTo) {
      Object.defineProperty(HTMLElement.prototype, "scrollTo", originalScrollTo);
    } else {
      delete (HTMLElement.prototype as { scrollTo?: unknown }).scrollTo;
    }
  });

  it("jumps to the latest events without animation once session history finishes loading", () => {
    const run = makeRun();
    useRunTimelineStore.setState({
      runs: { [run.id]: { ...run, steps: [makeStep({})] } },
      order: [run.id],
      loading: true,
    });
    render(<RunTimelinePanel />);

    act(() => {
      useRunTimelineStore.setState({ loading: false });
    });

    const jumpCalls = scrollToSpy.mock.calls.filter(
      (call) => (call[0] as { behavior?: string } | undefined)?.behavior === "auto",
    );
    expect(jumpCalls).toHaveLength(1);
  });

  it("re-arms follow mode when switching sessions after the user scrolled away", () => {
    const run = makeRun();
    const steps = [makeStep({ id: "step-1" })];
    useRunTimelineStore.setState({
      runs: { [run.id]: { ...run, steps } },
      order: [run.id],
      // Start mid-load so the mount-time effects don't burn the one legal
      // jump-to-latest before the user scrolls away.
      loading: true,
    });
    scrollToSpy.mockClear();
    render(<RunTimelinePanel />);
    act(() => {
      useRunTimelineStore.setState({ loading: false });
    });
    // The history-finished jump above is the legal one; everything after
    // this point must stay quiet until follow mode is re-armed.
    scrollToSpy.mockClear();

    // User scrolls away from the bottom (distance > 50px → following off).
    const scrollEl = screen.getByTestId("run-timeline-scroll");
    Object.defineProperty(scrollEl, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scrollEl, "clientHeight", { value: 400, configurable: true });
    fireEvent.scroll(scrollEl);

    // New events arrive while the user is away: badge, not forced scrolling.
    // (The mount-time smooth follow is legal; there must be no *jump*.)
    act(() => {
      useRunTimelineStore.setState((s) => ({
        runs: {
          ...s.runs,
          [run.id]: { ...run, steps: [...steps, makeStep({ id: "step-2" })] },
        },
      }));
    });
    expect(screen.getByTestId("run-timeline-new-events")).toBeInTheDocument();
    expect(
      scrollToSpy.mock.calls.filter(
        (call) => (call[0] as { behavior?: string } | undefined)?.behavior === "auto",
      ),
    ).toHaveLength(0);

    // Switch sessions; follow mode must be re-armed so the panel lands on
    // the newest events again. Same event count as before — the exact case
    // where eventCount alone cannot retrigger the scroll.
    act(() => {
      useSessionStore.setState({ currentSessionId: "session-2" });
    });
    const run2: AgentRun = { ...makeRun(), id: "run-0987654321", session_id: "session-2" };
    act(() => {
      useRunTimelineStore.setState({ loading: true });
    });
    act(() => {
      useRunTimelineStore.setState({
        runs: { [run2.id]: { ...run2, steps: [makeStep({ run_id: run2.id })] } },
        order: [run2.id],
        loading: false,
      });
    });

    const jumpCalls = scrollToSpy.mock.calls.filter(
      (call) => (call[0] as { behavior?: string } | undefined)?.behavior === "auto",
    );
    expect(jumpCalls).toHaveLength(1);
    expect(screen.queryByTestId("run-timeline-new-events")).not.toBeInTheDocument();
  });
});
