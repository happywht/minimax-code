/**
 * Git store — read-only cache of the working tree's git state.
 *
 * Backed by the ``git.*`` IPC namespace introduced in v0.3.0
 * (see ``docs/v0.3.0-design.md`` §3 and §4). The top-bar
 * ``GitStatusBar`` widget polls ``refreshStatus()`` on a short
 * interval; ``fetchDiff()`` and ``fetchLog()`` are on-demand and
 * mostly used by the code-review flow.
 *
 * The store is intentionally small — there is no need to
 * subscribe to push events because git state is a *snapshot* the
 * widget reads on a timer, not a stream the user is watching.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { GitDiffResult, GitLogResult, GitStatusResult } from "../types/ipc";
import { strings } from "../ui/strings";

export interface GitState {
  /** Latest known status snapshot — ``null`` until first refresh. */
  status: GitStatusResult | null;
  /** Last diff fetched via ``fetchDiff``. ``null`` until first call. */
  lastDiff: GitDiffResult | null;
  /** Last log fetched via ``fetchLog``. ``null`` until first call. */
  lastLog: GitLogResult | null;
  /** True while a ``git.*`` request is in flight. */
  loading: boolean;

  /**
   * Pull the current working-tree status. Called by the top-bar
   * widget on a short timer; safe to call repeatedly because the
   * backend shells out to ``git`` and returns a tiny payload.
   */
  refreshStatus: () => Promise<void>;
  /**
   * Fetch a diff for a scope. Stores the result in ``lastDiff``
   * so the code-review panel can re-render without re-fetching.
   */
  fetchDiff: (opts?: { scope?: "staged" | "branch" | "working"; ref?: string }) => Promise<GitDiffResult | null>;
  /**
   * Fetch the most recent N commits. ``n`` defaults to 10 on
   * the wire; pass an explicit value to override.
   */
  fetchLog: (opts?: { n?: number }) => Promise<GitLogResult | null>;
  /** Drop the cached snapshots (e.g. on workspace switch). */
  reset: () => void;
}

export const useGitStore = create<GitState>((set) => ({
  status: null,
  lastDiff: null,
  lastLog: null,
  loading: false,

  refreshStatus: async () => {
    set({ loading: true });
    try {
      const s = await typedIPC.gitStatus();
      set({ status: s, loading: false });
    } catch (err) {
      set({ loading: false });
      // Status calls are silent on failure — the widget shows a
      // "no data" state instead of a toast. The toast would
      // spam the user every poll.
      void err;
    }
  },

  fetchDiff: async (opts) => {
    set({ loading: true });
    try {
      const d = await typedIPC.gitDiff(opts ?? {});
      set({ lastDiff: d, loading: false });
      return d;
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.gitDiffFailed, message);
      return null;
    }
  },

  fetchLog: async (opts) => {
    set({ loading: true });
    try {
      const l = await typedIPC.gitLog(opts ?? {});
      set({ lastLog: l, loading: false });
      return l;
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.gitLogFailed, message);
      return null;
    }
  },

  reset: () => set({ status: null, lastDiff: null, lastLog: null, loading: false }),
}));
