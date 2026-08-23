/**
 * Tests for the ProgressPanel — the in-column progress list.
 *
 * Historical contract: this component used to be a floating overlay
 * with a built-in collapse pill. After the three-pane refactor it
 * is purely a content piece that lives inside <RightPanel />. The
 * chrome (header, collapse, "Agent" badge positioning) moved to the
 * parent; what remains here is the task list and the small agent
 * status indicator on the right of the section header.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProgressPanel } from "../src/components/right-panel/ProgressPanel";
import { useTaskStore } from "../src/stores";

describe("ProgressPanel", () => {
  beforeEach(() => {
    useTaskStore.setState({ tasks: {}, collapsed: false });
    localStorage.clear();
  });

  it("renders the progress section with the Agent status badge", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp")).toBeInTheDocument();
    expect(screen.getByText("进度")).toBeInTheDocument();
    expect(screen.getByTestId("progress-agent-status")).toBeInTheDocument();
  });

  it("shows the empty state when there are no tasks", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp-empty")).toBeInTheDocument();
    expect(screen.getByText("暂无运行中任务")).toBeInTheDocument();
  });

  it("renders a task row and dismisses it on click", () => {
    useTaskStore.getState().upsert({
      task_id: "t-1",
      progress: 0.42,
      status: "running",
      message: "thinking…",
    });
    render(<ProgressPanel testId="pp" />);
    const row = screen.getByTestId("task-row-t-1");
    expect(row).toBeInTheDocument();
    expect(screen.getByTestId("task-status-running")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("移除任务"));
    expect(useTaskStore.getState().tasks["t-1"]).toBeUndefined();
  });

  it("shows a running-count chip when at least one task is running", () => {
    useTaskStore.getState().upsert({
      task_id: "t-2",
      progress: 0.1,
      status: "running",
    });
    useTaskStore.getState().upsert({
      task_id: "t-3",
      progress: 1,
      status: "done",
    });
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp-running-count").textContent).toContain("1");
  });

  it("renders a done task's persisted result text (v1.2.0)", () => {
    // Mirror what entryFromRow produces after a ledger refresh: a done
    // entry carrying the run's result (e.g. a scheduled prompt's reply).
    useTaskStore.setState({
      tasks: {
        "t-r": {
          task_id: "t-r",
          status: "done",
          progress: 1,
          result: "LLM digest: 3 commits since yesterday",
          updated_at: Date.now(),
        },
      },
    });
    render(<ProgressPanel testId="pp" />);
    const result = screen.getByTestId("task-result-t-r");
    expect(result).toBeInTheDocument();
    expect(result.textContent).toContain("LLM digest: 3 commits");
  });

  it("keeps a persisted result across later progress ticks", () => {
    useTaskStore.setState({
      tasks: {
        "t-k": {
          task_id: "t-k",
          status: "done",
          progress: 1,
          result: "kept",
          updated_at: Date.now(),
        },
      },
    });
    // A late ``task.progress`` event carries no ``result`` — it must not
    // clobber the one hydrated from the ledger.
    useTaskStore.getState().upsert({
      task_id: "t-k",
      progress: 1,
      status: "done",
    });
    expect(useTaskStore.getState().tasks["t-k"].result).toBe("kept");
  });
});
