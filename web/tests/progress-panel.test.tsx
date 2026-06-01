/**
 * Tests for the ProgressPanel — collapsed / expanded states, sidecar
 * status, and task progress entries.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProgressPanel } from "../src/components/ProgressPanel";
import { useTaskStore } from "../src/stores";

describe("ProgressPanel", () => {
  beforeEach(() => {
    useTaskStore.setState({ tasks: {}, collapsed: false });
  });

  it("renders the expanded panel with the Agent section", () => {
    render(<ProgressPanel testId="pp" />);
    expect(screen.getByTestId("pp")).toBeInTheDocument();
    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
  });

  it("collapses to a pill when the collapse button is clicked", () => {
    render(<ProgressPanel testId="pp" />);
    fireEvent.click(screen.getByLabelText("Collapse progress panel"));
    expect(screen.getByTestId("pp-collapsed")).toBeInTheDocument();
  });

  it("re-expands when the collapsed pill is clicked", () => {
    useTaskStore.setState({ collapsed: true });
    render(<ProgressPanel testId="pp" />);
    const pill = screen.getByTestId("pp-collapsed");
    fireEvent.click(pill);
    expect(useTaskStore.getState().collapsed).toBe(false);
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
    fireEvent.click(screen.getByLabelText("Dismiss task"));
    expect(useTaskStore.getState().tasks["t-1"]).toBeUndefined();
  });
});
