/**
 * Tests for the SubAgentPanel and SubAgentResultCard components.
 *
 * The components read directly from ``useSubAgentStore``; tests
 * preload the store with hand-crafted runs and assert the rendered
 * DOM.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SubAgentPanel } from "../src/components/SubAgentPanel";
import { SubAgentResultCard } from "../src/components/SubAgentResultCard";
import { useSubAgentStore } from "../src/stores/subAgent";
import type { SubAgentRun } from "../src/types/ipc";

const SAMPLE_RUN: SubAgentRun = {
  run_id: "run_1",
  agent_id: "general",
  agent_name: "General",
  display_name: "My Helper",
  parent_session_id: "ses_1",
  context_message_id: "msg_1",
  prompt: "summarise this",
  status: "thinking",
  progress: 0.4,
  summary: "thinking about: summarise this",
  started_at: 100,
  updated_at: 200,
};

const COMPLETED_RUN: SubAgentRun = {
  run_id: "run_2",
  agent_id: "researcher",
  agent_name: "Researcher",
  prompt: "find the bug",
  status: "completed",
  progress: 1,
  summary: "done",
  text: "the bug is on line 42",
  started_at: 300,
  updated_at: 400,
  finished_at: 400,
};

beforeEach(() => {
  useSubAgentStore.setState({ runs: {}, subscribed: false, error: null });
});

describe("SubAgentPanel", () => {
  it("renders the empty state when no runs are tracked", () => {
    render(<SubAgentPanel autoInit={false} />);
    expect(screen.getByTestId("sub-agent-panel-empty")).toBeInTheDocument();
  });

  it("renders a row per in-flight run with progress bar + status pill", () => {
    useSubAgentStore.setState({ runs: { [SAMPLE_RUN.run_id]: SAMPLE_RUN } });
    render(<SubAgentPanel autoInit={false} />);
    expect(screen.getByTestId("sub-agent-panel-list")).toBeInTheDocument();
    const row = screen.getByTestId(`sub-agent-panel-row-${SAMPLE_RUN.run_id}`);
    expect(row).toBeInTheDocument();
    expect(screen.getByTestId(`sub-agent-panel-row-${SAMPLE_RUN.run_id}-name`).textContent).toBe("My Helper");
    // Progress bar width is set to the percent value of the run.
    const bar = screen.getByTestId(`sub-agent-panel-row-${SAMPLE_RUN.run_id}-progress-bar`) as HTMLElement;
    expect(bar.style.width).toBe("40%");
  });

  it("expands the row to show prompt + text + run_id when clicked", () => {
    useSubAgentStore.setState({ runs: { [COMPLETED_RUN.run_id]: COMPLETED_RUN } });
    render(<SubAgentPanel autoInit={false} />);
    fireEvent.click(screen.getByTestId(`sub-agent-panel-row-${COMPLETED_RUN.run_id}-header`));
    expect(screen.getByTestId(`sub-agent-panel-row-${COMPLETED_RUN.run_id}-detail`)).toBeInTheDocument();
    expect(screen.getByTestId(`sub-agent-panel-row-${COMPLETED_RUN.run_id}-text`).textContent).toContain("the bug is on line 42");
  });

  it("clears completed runs when the clear button is clicked", () => {
    useSubAgentStore.setState({ runs: { [COMPLETED_RUN.run_id]: COMPLETED_RUN } });
    render(<SubAgentPanel autoInit={false} />);
    fireEvent.click(screen.getByTestId("sub-agent-panel-clear"));
    expect(useSubAgentStore.getState().runs).toEqual({});
  });
});

describe("SubAgentResultCard", () => {
  it("renders a card with agent name, status pill, and summary", () => {
    useSubAgentStore.setState({ runs: { [COMPLETED_RUN.run_id]: COMPLETED_RUN } });
    render(<SubAgentResultCard runId={COMPLETED_RUN.run_id} />);
    expect(screen.getByTestId("sub-agent-result-name").textContent).toBe("Researcher");
    expect(screen.getByTestId("sub-agent-result-status").textContent).toBe("completed");
    expect(screen.getByTestId("sub-agent-result-summary").textContent).toBe("done");
  });

  it("expands to show the final text when the chevron is clicked", () => {
    useSubAgentStore.setState({ runs: { [COMPLETED_RUN.run_id]: COMPLETED_RUN } });
    render(<SubAgentResultCard runId={COMPLETED_RUN.run_id} />);
    fireEvent.click(screen.getByTestId("sub-agent-result-toggle"));
    expect(screen.getByTestId("sub-agent-result-text").textContent).toContain("the bug is on line 42");
  });

  it("renders a placeholder when the run has dropped out of the store", () => {
    render(<SubAgentResultCard runId="run_unknown" />);
    expect(screen.getByTestId("sub-agent-result-missing")).toBeInTheDocument();
  });

  it("invokes onOpenRun when the open-run button is clicked", () => {
    useSubAgentStore.setState({ runs: { [COMPLETED_RUN.run_id]: COMPLETED_RUN } });
    const onOpenRun = (run: SubAgentRun) => {
      expect(run.run_id).toBe(COMPLETED_RUN.run_id);
    };
    render(<SubAgentResultCard runId={COMPLETED_RUN.run_id} onOpenRun={onOpenRun} />);
    fireEvent.click(screen.getByTestId("sub-agent-result-open"));
  });
});
