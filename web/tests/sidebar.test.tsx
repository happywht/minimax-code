/**
 * Tests for the Sidebar — verify the brand, the "新任务" button
 * creates a session, and the nav items switch the filter.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Sidebar } from "../src/components/Sidebar";
import { useSessionStore } from "../src/stores";

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listSessions: vi.fn(async () => ({ sessions: [] })),
      createSession: vi.fn(async (_opts: { title?: string } = {}) => ({
        session_id: "ses_test_1",
      })),
      createWorktreeSession: vi.fn(async (_opts: { title?: string; base_ref?: string } = {}) => ({
        session_id: "ses_wt_1",
        session: {
          id: "ses_wt_1",
          title: "Worktree task",
          archived: false,
          created_at: Date.now(),
          updated_at: Date.now(),
          model_id: null,
          workspace_mode: "worktree",
          workspace_path: "C:/tmp/worktrees/ses_wt_1",
          base_branch: "HEAD",
        },
      })),
    },
  };
});

describe("Sidebar", () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      loading: false,
      filter: "all",
    });
  });

  it("renders the brand and the new task button", async () => {
    render(<Sidebar />);
    await waitFor(() => {
      expect(screen.getByTestId("sidebar-brand")).toBeInTheDocument();
    });
    expect(screen.getByTestId("sidebar-new-task")).toBeInTheDocument();
  });

  it("creates a new session when the new task button is clicked", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<Sidebar />);
    fireEvent.click(screen.getByTestId("sidebar-new-task"));
    await waitFor(() => {
      expect(typedIPC.createSession).toHaveBeenCalledWith({ title: "New task" });
    });
    expect(useSessionStore.getState().currentSessionId).toBe("ses_test_1");
  });

  it("creates a worktree session when the worktree task button is clicked", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<Sidebar />);
    fireEvent.click(screen.getByTestId("sidebar-new-worktree-task"));
    await waitFor(() => {
      expect(typedIPC.createWorktreeSession).toHaveBeenCalledWith({
        title: "Worktree task",
        base_ref: "HEAD",
      });
    });
    expect(useSessionStore.getState().currentSessionId).toBe("ses_wt_1");
    expect(useSessionStore.getState().sessions[0].workspace_mode).toBe("worktree");
  });

  it("switches the session filter when a nav item is clicked", async () => {
    render(<Sidebar />);
    await waitFor(() => {
      expect(screen.getByTestId("sidebar-nav-skills")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("sidebar-nav-skills"));
    expect(useSessionStore.getState().filter).toBe("skills");
  });

  it("lists known sessions in the history section", () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_1",
          title: "Refactor",
          archived: false,
          created_at: 1,
          updated_at: 2,
          model_id: null,
        },
        {
          id: "ses_2",
          title: "Old",
          archived: false,
          created_at: 1,
          updated_at: 1,
          model_id: null,
          workspace_mode: "worktree",
          workspace_path: "C:/tmp/worktrees/ses_2",
        },
      ],
    });
    render(<Sidebar />);
    expect(screen.getByTestId("sidebar-session-ses_1")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-session-ses_2")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-session-workspace-ses_2")).toHaveTextContent("WT");
  });
});
