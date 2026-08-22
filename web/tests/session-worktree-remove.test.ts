/**
 * Tests for sessionStore.remove's worktree branch (P1-4) — deleting a
 * worktree session must clean the on-disk worktree via
 * workspace.delete_worktree before dropping the DB row.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { waitFor } from "@testing-library/react";

// vi.mock is hoisted — create the mock fns via vi.hoisted so the
// factory can reference them safely.
const { deleteSession, deleteWorktree, toastError } = vi.hoisted(() => ({
  deleteSession: vi.fn(),
  deleteWorktree: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("../src/ipc", () => ({
  typedIPC: {
    deleteSession,
    deleteWorktree,
  },
}));
vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { success: vi.fn(), error: toastError, info: vi.fn() },
}));

import { useSessionStore } from "../src/stores";

const WT = {
  id: "ses_wt_1",
  title: "Worktree 任务",
  archived: false,
  created_at: 1,
  updated_at: 2,
  model_id: null,
  workspace_mode: "worktree" as const,
  workspace_path: "C:/data/worktrees/ses_wt_1",
  base_branch: "HEAD",
};
const LOCAL = {
  id: "ses_local_1",
  title: "本地任务",
  archived: false,
  created_at: 1,
  updated_at: 2,
  model_id: null,
};

describe("sessionStore.remove worktree cleanup (P1-4)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deleteSession.mockResolvedValue({});
    deleteWorktree.mockResolvedValue({ ok: true });
    window.localStorage.clear();
    useSessionStore.setState({
      sessions: [WT, LOCAL],
      currentSessionId: null,
    });
  });

  it("cleans the worktree directory before deleting a worktree session", async () => {
    await useSessionStore.getState().remove(WT.id);

    expect(deleteWorktree).toHaveBeenCalledWith(WT.id);
    expect(deleteSession).toHaveBeenCalledWith(WT.id);
    // Cleanup must happen first — deleting the row first would orphan
    // the directory with no way to reach it from the UI.
    expect(deleteWorktree.mock.invocationCallOrder[0]).toBeLessThan(
      deleteSession.mock.invocationCallOrder[0],
    );
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual([LOCAL.id]);
    expect(toastError).not.toHaveBeenCalled();
  });

  it("still deletes the row when worktree cleanup fails, with a degraded toast", async () => {
    deleteWorktree.mockRejectedValue(new Error("git worktree remove failed"));

    await useSessionStore.getState().remove(WT.id);

    expect(deleteSession).toHaveBeenCalledWith(WT.id);
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual([LOCAL.id]);
    expect(toastError).toHaveBeenCalled();
  });

  it("does not touch delete_worktree for a plain local session", async () => {
    await useSessionStore.getState().remove(LOCAL.id);

    expect(deleteWorktree).not.toHaveBeenCalled();
    expect(deleteSession).toHaveBeenCalledWith(LOCAL.id);
    await waitFor(() => {
      expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual([WT.id]);
    });
  });
});
