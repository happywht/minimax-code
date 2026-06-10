import { create } from "zustand";
import { typedIPC } from "../ipc";
import type {
  PatchHunkOperationParams,
  PatchHunkOperationResult,
  PatchPreviewResult,
} from "../types/ipc";

export type PatchPreviewScope = "working" | "staged" | "branch";

export interface PatchPreviewState {
  scope: PatchPreviewScope;
  result: PatchPreviewResult | null;
  loading: boolean;
  error: string | null;
  setScope: (scope: PatchPreviewScope) => void;
  refresh: (opts?: { scope?: PatchPreviewScope; ref?: string }) => Promise<PatchPreviewResult | null>;
  applyHunk: (opts: PatchHunkOperationParams) => Promise<PatchHunkOperationResult>;
  revertHunk: (opts: PatchHunkOperationParams) => Promise<PatchHunkOperationResult>;
  reset: () => void;
}

export const usePatchPreviewStore = create<PatchPreviewState>((set, get) => ({
  scope: "working",
  result: null,
  loading: false,
  error: null,

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

  reset: () => set({ scope: "working", result: null, loading: false, error: null }),
}));
