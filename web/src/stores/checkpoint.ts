/**
 * Checkpoint store — session-scoped workspace snapshot cache.
 *
 * The CheckpointPanel used to keep its list / diffs / loading state in
 * component ``useState``, which meant every RightPanel tab switch
 * unmounted the panel and threw the state away (re-fetch on return,
 * lost expanded diff). Moving the state here keeps it alive across
 * tab switches for the lifetime of the page.
 *
 * Form-level ephemera (label/message drafts, the ``creating`` flag)
 * intentionally stay in the component — they are not worth surviving
 * a tab switch.
 */
import { create } from "zustand";
import { typedIPC } from "../ipc";
import type { Checkpoint } from "../types/ipc";
import { strings } from "../ui/strings";

export interface CheckpointState {
  /** Checkpoint list per session id. */
  bySession: Record<string, Checkpoint[]>;
  /** Diff text per checkpoint id (patch, "no diff" note or load error). */
  diffs: Record<string, string>;
  /** Per-checkpoint diff fetch in flight. */
  diffLoading: Record<string, boolean>;
  /** Which checkpoint row has its diff expanded. */
  expandedDiffId: string | null;
  /** List fetch in flight, per session id. */
  loading: Record<string, boolean>;
  /** Last list-load error per session id (rendered as inline banner). */
  loadError: Record<string, string | null>;

  /**
   * Load the checkpoint list for a session. Cached unless ``force``:
   * a cached hit returns without a round-trip so re-mounting the
   * panel is free.
   */
  load: (sessionId: string, force?: boolean) => Promise<void>;
  /** Fetch (once) and cache the diff for one checkpoint. */
  ensureDiff: (checkpointId: string) => Promise<void>;
  /** Expand/collapse one checkpoint's diff (fetches on first expand). */
  toggleExpand: (checkpointId: string) => void;
  /**
   * Drop the cached list and re-fetch — called after create/delete
   * so the panel reflects the backend immediately.
   */
  invalidate: (sessionId: string) => Promise<void>;
  /** Reset state — used by tests. */
  reset: () => void;
}

export const useCheckpointStore = create<CheckpointState>((set, get) => ({
  bySession: {},
  diffs: {},
  diffLoading: {},
  expandedDiffId: null,
  loading: {},
  loadError: {},

  load: async (sessionId, force = false) => {
    if (!force && get().bySession[sessionId] !== undefined) return;
    set((s) => ({ loading: { ...s.loading, [sessionId]: true } }));
    try {
      const result = await typedIPC.listCheckpoints(sessionId);
      set((s) => ({
        bySession: { ...s.bySession, [sessionId]: result.checkpoints },
        loadError: { ...s.loadError, [sessionId]: null },
      }));
    } catch (err) {
      // Keep any previously cached list — the banner is rendered
      // alongside it so stale data is never silently shown as fresh.
      const msg = err instanceof Error ? err.message : String(err);
      set((s) => ({
        loadError: { ...s.loadError, [sessionId]: msg },
      }));
    } finally {
      set((s) => ({ loading: { ...s.loading, [sessionId]: false } }));
    }
  },

  ensureDiff: async (checkpointId) => {
    const { diffs, diffLoading } = get();
    if (diffs[checkpointId] !== undefined || diffLoading[checkpointId]) return;
    set((s) => ({ diffLoading: { ...s.diffLoading, [checkpointId]: true } }));
    try {
      const result = await typedIPC.diffCheckpoint(checkpointId);
      const text = result.available
        ? result.patch
        : strings.rightPanel.checkpoint.noDiff;
      set((s) => ({
        diffs: { ...s.diffs, [checkpointId]: text },
        diffLoading: { ...s.diffLoading, [checkpointId]: false },
      }));
    } catch (err) {
      // Same shape as the panel's old behaviour: a failed diff load
      // renders as the error text in place of the patch.
      const msg = err instanceof Error ? err.message : String(err);
      set((s) => ({
        diffs: { ...s.diffs, [checkpointId]: strings.rightPanel.checkpoint.diffLoadError(msg) },
        diffLoading: { ...s.diffLoading, [checkpointId]: false },
      }));
    }
  },

  toggleExpand: (checkpointId) => {
    const expanded = get().expandedDiffId;
    if (expanded === checkpointId) {
      set({ expandedDiffId: null });
      return;
    }
    set({ expandedDiffId: checkpointId });
    void get().ensureDiff(checkpointId);
  },

  invalidate: async (sessionId) => {
    await get().load(sessionId, true);
    // Drop diff cache entries for checkpoints that no longer exist
    // (e.g. deleted) so stale patches can't render after re-expand.
    const list = get().bySession[sessionId] ?? [];
    const alive = new Set(list.map((c) => c.id));
    set((s) => {
      const diffs: Record<string, string> = {};
      for (const [id, text] of Object.entries(s.diffs)) {
        if (alive.has(id)) diffs[id] = text;
      }
      return { diffs };
    });
  },

  reset: () =>
    set({
      bySession: {},
      diffs: {},
      diffLoading: {},
      expandedDiffId: null,
      loading: {},
      loadError: {},
    }),
}));
