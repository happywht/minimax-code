import { beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
});
