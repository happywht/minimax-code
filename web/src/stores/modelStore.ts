/**
 * Model store — list of available models + the currently-selected one.
 * Persisted to the Python sidecar (which in turn writes to SQLite).
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import type { ModelInfo } from "../types/ipc";

export type ModelEntry = ModelInfo;

export interface ModelState {
  models: ModelEntry[];
  current: string | null;
  loading: boolean;

  refresh: () => Promise<void>;
  setCurrent: (id: string) => Promise<void>;
}

export const useModelStore = create<ModelState>((set) => ({
  models: [],
  current: null,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listModels();
      set({ models: r.models, current: r.current, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load models", message);
    }
  },

  setCurrent: async (id: string) => {
    try {
      const r = await typedIPC.setCurrentModel(id);
      set({ current: r.current });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to switch model", message);
    }
  },
}));
