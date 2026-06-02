/**
 * Tests for the RightPanel — the right column of the three-pane
 * shell. Covers the Progress section, the Agent Team section,
 * the section-collapse toggles, and the panel-level collapse pill.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RightPanel } from "../src/components/RightPanel";
import { useTaskStore } from "../src/stores";
import type { AgentInfo } from "../src/types/ipc";

const SAMPLE_AGENTS: AgentInfo[] = [
  {
    id: "agent_explorer",
    name: "Explorer",
    description: "searches the codebase",
    enabled: true,
  },
  {
    id: "agent_coder",
    name: "Coder",
    description: "writes patches",
    enabled: true,
  },
];

beforeEach(() => {
  useTaskStore.setState({ tasks: {}, collapsed: false });
  localStorage.clear();
});

describe("RightPanel — chrome", () => {
  it("renders the right panel with the workspace header and both section headers", () => {
    render(<RightPanel initialAgents={[]} />);
    expect(screen.getByTestId("right-panel")).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-progress-header")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-team-header")).toBeInTheDocument();
  });

  it("collapses the entire panel to a strip when the chevron is clicked", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-collapse"));
    expect(screen.getByTestId("right-panel-collapsed")).toBeInTheDocument();
    // The original section headers are gone.
    expect(screen.queryByTestId("right-panel-progress-header")).toBeNull();
  });

  it("re-expands the panel when the strip chevron is clicked", () => {
    render(<RightPanel defaultCollapsed initialAgents={[]} />);
    expect(screen.queryByTestId("right-panel")).toBeNull();
    fireEvent.click(screen.getByTestId("right-panel-expand"));
    expect(screen.getByTestId("right-panel")).toBeInTheDocument();
  });
});

describe("RightPanel — Progress section", () => {
  it("shows the progress section body when expanded (default)", () => {
    render(<RightPanel initialAgents={[]} />);
    expect(screen.getByTestId("right-panel-progress-body")).toBeInTheDocument();
  });

  it("hides the progress section body when the section header is clicked twice", () => {
    render(<RightPanel initialAgents={[]} />);
    const header = screen.getByTestId("right-panel-progress-header");
    fireEvent.click(header);
    expect(screen.queryByTestId("right-panel-progress-body")).toBeNull();
    fireEvent.click(header);
    expect(screen.getByTestId("right-panel-progress-body")).toBeInTheDocument();
  });
});

describe("RightPanel — Agent Team section", () => {
  it("shows the empty state when no agents are returned", () => {
    render(<RightPanel initialAgents={[]} />);
    expect(screen.getByTestId("right-panel-team-empty")).toBeInTheDocument();
    expect(screen.getByText("No sub-agents")).toBeInTheDocument();
  });

  it("renders one card per agent with name + idle status dot", () => {
    render(<RightPanel initialAgents={SAMPLE_AGENTS} />);
    expect(screen.getByTestId("right-panel-team-list")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-team-card-agent_explorer")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-team-card-agent_coder")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-team-card-agent_explorer-name").textContent).toBe("Explorer");
    expect(screen.getByTestId("right-panel-team-card-agent_coder-name").textContent).toBe("Coder");
  });

  it("marks an agent as running when a matching task exists in the store", () => {
    useTaskStore.getState().upsert({
      task_id: "agent_explorer",
      progress: 0.5,
      status: "running",
      message: "scanning src/",
    });
    render(<RightPanel initialAgents={SAMPLE_AGENTS} />);
    const card = screen.getByTestId("right-panel-team-card-agent_explorer");
    expect(card.querySelector('[data-testid="agent-status-running"]')).toBeTruthy();
    expect(screen.getByTestId("right-panel-team-card-agent_explorer-task").textContent).toBe("scanning src/");
  });

  it("collapses the team section via the section header", () => {
    render(<RightPanel initialAgents={SAMPLE_AGENTS} />);
    fireEvent.click(screen.getByTestId("right-panel-team-header"));
    expect(screen.queryByTestId("right-panel-team-list")).toBeNull();
  });

  it("loads agents from the IPC client when no initialAgents is given", async () => {
    const loadAgents = vi.fn(async () => SAMPLE_AGENTS);
    render(<RightPanel loadAgents={loadAgents} />);
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId("right-panel-team-card-agent_explorer")).toBeInTheDocument();
  });

  it("shows the error state when the loader throws", async () => {
    const loadAgents = vi.fn(async () => {
      throw new Error("boom");
    });
    render(<RightPanel loadAgents={loadAgents} />);
    await waitFor(() => {
      expect(screen.getByTestId("right-panel-team-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Failed to load agents")).toBeInTheDocument();
  });

  it("refetches the agent list when the refresh action is clicked", async () => {
    const loadAgents = vi.fn(async () => SAMPLE_AGENTS);
    render(<RightPanel loadAgents={loadAgents} />);
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByTestId("right-panel-team-refresh"));
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(2);
    });
  });
});
