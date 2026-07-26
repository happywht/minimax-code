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
    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByTestId("progress-agent-status")).toBeInTheDocument();
  });

  it("shows the empty state when there are no tasks", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp-empty")).toBeInTheDocument();
    expect(screen.getByText("No active tasks")).toBeInTheDocument();
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
    fireEvent.click(screen.getByLabelText("Dismiss task"));
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
});
