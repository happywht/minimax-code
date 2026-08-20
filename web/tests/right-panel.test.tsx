/**
 * Tests for the RightPanel — the right column of the three-pane
 * shell. Covers the tabbed inspector model, the Agent Team panel,
 * and the panel-level collapse pill.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RightPanel } from "../src/components/RightPanel";
import {
  usePatchPreviewStore,
  usePermissionStore,
  useRunnerStore,
  useRunTimelineStore,
  useSubAgentStore,
  useTaskStore,
  useTerminalStore,
} from "../src/stores";
import type { AgentInfo, SubAgentRun } from "../src/types/ipc";

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

function sampleSubAgentRun(id: string, updatedAt: number): SubAgentRun {
  return {
    run_id: id,
    agent_id: "agent_explorer",
    agent_name: "Explorer",
    parent_session_id: "session_1",
    prompt: "inspect the repo",
    status: "started",
    progress: 0.1,
    summary: "",
    started_at: updatedAt,
    updated_at: updatedAt,
  };
}

beforeEach(() => {
  useTaskStore.setState({ tasks: {}, collapsed: false });
  useSubAgentStore.getState().reset();
  useRunTimelineStore.getState().reset();
  usePermissionStore.setState({ pending: {}, alwaysAllow: false, rules: [], loading: false });
  usePatchPreviewStore.getState().reset();
  useTerminalStore.getState().reset();
  useRunnerStore.getState().reset();
  localStorage.clear();
});

describe("RightPanel — chrome", () => {
  it("renders the right panel as a tabbed inspector", () => {
    render(<RightPanel initialAgents={[]} />);
    expect(screen.getByTestId("right-panel")).toBeInTheDocument();
    expect(screen.getByText("检查器")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-timeline")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("right-panel-tab-diff")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-agents")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-terminal")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-runner")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-timeline-body")).toBeInTheDocument();
    expect(screen.queryByTestId("right-panel-patch-body")).toBeNull();
  });

  it("collapses the entire panel to a strip when the chevron is clicked", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-collapse"));
    expect(screen.getByTestId("right-panel-collapsed")).toBeInTheDocument();
    expect(screen.queryByTestId("right-panel-tab-progress")).toBeNull();
  });

  it("re-expands the panel when the strip chevron is clicked", () => {
    render(<RightPanel defaultCollapsed initialAgents={[]} />);
    expect(screen.queryByTestId("right-panel")).toBeNull();
    fireEvent.click(screen.getByTestId("right-panel-expand"));
    expect(screen.getByTestId("right-panel")).toBeInTheDocument();
  });
});

describe("RightPanel — run follow", () => {
  it("auto-switches to the sub-agent tab when sub-agent activity appears", async () => {
    render(<RightPanel initialAgents={[]} />);
    expect(screen.getByTestId("right-panel-tab-timeline")).toHaveAttribute("aria-selected", "true");

    act(() => {
      useSubAgentStore.getState().register(sampleSubAgentRun("run_1", 100));
    });

    await waitFor(() => {
      expect(screen.getByTestId("right-panel-tab-subagents")).toHaveAttribute("aria-selected", "true");
    });
    expect(screen.getByTestId("right-panel-sub-body")).toBeInTheDocument();
  });

  it("does not steal focus after the user manually pins an inspector tab", async () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-diff"));
    expect(screen.getByTestId("right-panel-tab-diff")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("right-panel-resume-follow")).toBeInTheDocument();

    act(() => {
      useSubAgentStore.getState().register(sampleSubAgentRun("run_1", 100));
    });

    await waitFor(() => {
      expect(screen.getByTestId("right-panel-tab-diff")).toHaveAttribute("aria-selected", "true");
    });
    expect(screen.queryByTestId("right-panel-sub-body")).toBeNull();
  });

  it("resumes auto-following after the user clicks Follow run", async () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-diff"));
    fireEvent.click(screen.getByTestId("right-panel-resume-follow"));

    act(() => {
      useSubAgentStore.getState().register(sampleSubAgentRun("run_1", 100));
    });

    await waitFor(() => {
      expect(screen.getByTestId("right-panel-tab-subagents")).toHaveAttribute("aria-selected", "true");
    });
    expect(screen.queryByTestId("right-panel-resume-follow")).toBeNull();
  });
});

describe("RightPanel — Progress section", () => {
  it("shows the progress body when the progress tab is selected", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-progress"));
    expect(screen.getByTestId("right-panel-progress-body")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-progress")).toHaveAttribute("aria-selected", "true");
  });

  it("shows the terminal body when the terminal tab is selected", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-terminal"));
    expect(screen.getByTestId("right-panel-terminal-body")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-terminal")).toHaveAttribute("aria-selected", "true");
  });

  it("shows the runner body when the runner tab is selected", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-runner"));
    expect(screen.getByTestId("right-panel-runner-body")).toBeInTheDocument();
    expect(screen.getByTestId("right-panel-tab-runner")).toHaveAttribute("aria-selected", "true");
  });

  it("switches between inspector tabs without keeping stale bodies mounted", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-progress"));
    expect(screen.getByTestId("right-panel-progress-body")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("right-panel-tab-diff"));
    expect(screen.queryByTestId("right-panel-progress-body")).toBeNull();
    expect(screen.getByTestId("right-panel-patch-body")).toBeInTheDocument();
  });
});

describe("RightPanel — Agent Team section", () => {
  it("shows the empty state when no agents are returned", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    expect(screen.getByTestId("right-panel-team-empty")).toBeInTheDocument();
    expect(screen.getByText("暂无子 Agent")).toBeInTheDocument();
  });

  it("renders one card per agent with name + idle status dot", () => {
    render(<RightPanel initialAgents={SAMPLE_AGENTS} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
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
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    const card = screen.getByTestId("right-panel-team-card-agent_explorer");
    expect(card.querySelector('[data-testid="agent-status-running"]')).toBeTruthy();
    expect(screen.getByTestId("right-panel-team-card-agent_explorer-task").textContent).toBe("scanning src/");
  });

  it("unmounts the team panel when another tab is selected", () => {
    render(<RightPanel initialAgents={SAMPLE_AGENTS} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    expect(screen.getByTestId("right-panel-team-list")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("right-panel-tab-timeline"));
    expect(screen.queryByTestId("right-panel-team-list")).toBeNull();
  });

  it("loads agents from the IPC client when no initialAgents is given", async () => {
    const loadAgents = vi.fn(async () => SAMPLE_AGENTS);
    render(<RightPanel loadAgents={loadAgents} />);
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    expect(screen.getByTestId("right-panel-team-card-agent_explorer")).toBeInTheDocument();
  });

  it("shows the error state when the loader throws", async () => {
    const loadAgents = vi.fn(async () => {
      throw new Error("boom");
    });
    render(<RightPanel loadAgents={loadAgents} />);
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    await waitFor(() => {
      expect(screen.getByTestId("right-panel-team-error")).toBeInTheDocument();
    });
    expect(screen.getByText("加载 Agent 失败")).toBeInTheDocument();
  });

  it("refetches the agent list when the refresh action is clicked", async () => {
    const loadAgents = vi.fn(async () => SAMPLE_AGENTS);
    render(<RightPanel loadAgents={loadAgents} />);
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByTestId("right-panel-tab-agents"));
    fireEvent.click(screen.getByTestId("right-panel-team-refresh"));
    await waitFor(() => {
      expect(loadAgents).toHaveBeenCalledTimes(2);
    });
  });
});
