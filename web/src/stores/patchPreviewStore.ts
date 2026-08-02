import { create } from "zustand";
import { typedIPC } from "../ipc";
import type {
  PatchApplyAllResult,
  PatchFileOperationParams,
  PatchFileOperationResult,
  PatchHunkOperationParams,
  PatchHunkOperationResult,
  PatchPreviewResult,
  PatchSaveSnapshotResult,
} from "../types/ipc";

export type PatchPreviewScope = "working" | "staged" | "branch";

export interface PatchPreviewState {
  scope: PatchPreviewScope;
  result: PatchPreviewResult | null;
  loading: boolean;
  error: string | null;
  globalBusy: boolean;
  setScope: (scope: PatchPreviewScope) => void;
  refresh: (opts?: { scope?: PatchPreviewScope; ref?: string }) => Promise<PatchPreviewResult | null>;
  applyHunk: (opts: PatchHunkOperationParams) => Promise<PatchHunkOperationResult>;
  revertHunk: (opts: PatchHunkOperationParams) => Promise<PatchHunkOperationResult>;
  applyFile: (opts: PatchFileOperationParams) => Promise<PatchFileOperationResult>;
  revertFile: (opts: PatchFileOperationParams) => Promise<PatchFileOperationResult>;
  applyAll: (opts?: { scope?: PatchPreviewScope }) => Promise<PatchApplyAllResult>;
  revertAll: (opts?: { scope?: PatchPreviewScope }) => Promise<PatchApplyAllResult>;
  saveSnapshot: () => Promise<PatchSaveSnapshotResult>;
  reset: () => void;
}

export const usePatchPreviewStore = create<PatchPreviewState>((set, get) => ({
  scope: "working",
  result: null,
  loading: false,
  error: null,
  globalBusy: false,

  setScope: (scope) => set({ scope }),

  refresh: async (opts) => {
    const scope = opts?.scope ?? get().scope;
    set({ scope, loading: true, error: null });
    try {
      const result = await typedIPC.patchPreview({ scope, ref: opts?.ref });
      set({ result, loading: false });
      return result;
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      set({ loading: false, error });
      return null;
    }
  },

  applyHunk: async (opts) => typedIPC.patchApplyHunk(opts),

  revertHunk: async (opts) => typedIPC.patchRevertHunk(opts),

  applyFile: async (opts) => typedIPC.patchApplyFile(opts),

  revertFile: async (opts) => typedIPC.patchRevertFile(opts),

  applyAll: async (opts) => {
    set({ globalBusy: true });
    try {
      const scope = opts?.scope ?? get().scope;
      if (scope === "branch") {
        return { ok: false, operation: "apply_all", scope, applied: [], failed: [] };
      }
      const result = await typedIPC.patchApplyAll({ scope });
      return result;
    } finally {
      set({ globalBusy: false });
    }
  },

  revertAll: async (opts) => {
    set({ globalBusy: true });
    try {
      const scope = opts?.scope ?? get().scope;
      if (scope === "branch") {
        return { ok: false, operation: "revert_all", scope, applied: [], failed: [] };
      }
      const result = await typedIPC.patchRevertAll({ scope });
      return result;
    } finally {
      set({ globalBusy: false });
    }
  },

  saveSnapshot: async () => {
    set({ globalBusy: true });
    try {
      const result = await typedIPC.patchSaveSnapshot();
      return result;
    } finally {
      set({ globalBusy: false });
    }
  },

  reset: () => set({ scope: "working", result: null, loading: false, error: null, globalBusy: false }),
}));
