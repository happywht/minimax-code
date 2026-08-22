/**
 * Tests for the Worktrees tab (P1-4) — list worktree sessions, empty
 * state, and the confirm-gated cleanup flow over workspace.delete_worktree.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// vi.mock is hoisted — create the mock fns via vi.hoisted so the
// factory can reference them safely.
const { listWorktreeSessions, deleteWorktree, confirmMock } = vi.hoisted(() => ({
  listWorktreeSessions: vi.fn(),
  deleteWorktree: vi.fn(),
  confirmMock: vi.fn(),
}));

vi.mock("../src/ipc", () => ({
  typedIPC: {
    listWorktreeSessions,
    deleteWorktree,
  },
}));
vi.mock("../src/components/modals/ConfirmationDialog", () => ({
  requestConfirmation: confirmMock,
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { WorktreesTab } from "../src/components/settings/WorktreesTab";

const WT_A = {
  id: "ses_wt_a",
  title: "试验田 A",
  archived: false,
  created_at: 1,
  updated_at: 2,
  model_id: null,
  workspace_mode: "worktree" as const,
  workspace_path: "C:/data/worktrees/ses_wt_a",
  base_branch: "HEAD",
};
const WT_B = {
  id: "ses_wt_b",
  title: "重构分支 B",
  archived: false,
  created_at: 1,
  updated_at: 3,
  model_id: null,
  workspace_mode: "worktree" as const,
  workspace_path: "C:/data/worktrees/ses_wt_b",
  base_branch: "main",
};

describe("WorktreesTab (P1-4)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listWorktreeSessions.mockResolvedValue({ sessions: [] });
    deleteWorktree.mockResolvedValue({ ok: true });
    confirmMock.mockResolvedValue(true);
  });


  it("renders rows with title, path and base ref", async () => {
    listWorktreeSessions.mockResolvedValue({ sessions: [WT_A, WT_B] });
    render(<WorktreesTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-worktree-row-ses_wt_a")).toBeInTheDocument();
    });
    expect(screen.getByTestId("settings-worktree-row-ses_wt_b")).toBeInTheDocument();
    expect(screen.getByTestId("settings-worktree-path-ses_wt_a")).toHaveTextContent(
      "C:/data/worktrees/ses_wt_a",
    );
    expect(screen.getByText("重构分支 B")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
  });

  it("shows the empty state when there are no worktree sessions", async () => {
    render(<WorktreesTab />);
    await waitFor(() => {
      expect(screen.getByText("暂无 Worktree 任务")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("settings-worktrees-list")).not.toBeInTheDocument();
  });

  it("cleans up a worktree after confirmation and drops the row", async () => {
    listWorktreeSessions.mockResolvedValue({ sessions: [WT_A, WT_B] });
    render(<WorktreesTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-worktree-row-ses_wt_a")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("settings-worktree-cleanup-ses_wt_a"));
    await waitFor(() => expect(deleteWorktree).toHaveBeenCalledWith("ses_wt_a"));
    expect(confirmMock).toHaveBeenCalled();
    // Only the cleaned row disappears; the other worktree stays listed.
    expect(screen.queryByTestId("settings-worktree-row-ses_wt_a")).not.toBeInTheDocument();
    expect(screen.getByTestId("settings-worktree-row-ses_wt_b")).toBeInTheDocument();
  });

  it("does nothing when the confirmation is declined", async () => {
    confirmMock.mockResolvedValue(false);
    listWorktreeSessions.mockResolvedValue({ sessions: [WT_A] });
    render(<WorktreesTab />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-worktree-cleanup-ses_wt_a")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("settings-worktree-cleanup-ses_wt_a"));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(deleteWorktree).not.toHaveBeenCalled();
    expect(screen.getByTestId("settings-worktree-row-ses_wt_a")).toBeInTheDocument();
  });
});
