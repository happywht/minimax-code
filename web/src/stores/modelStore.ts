/**
 * Model store — list of available models + the currently-selected one.
 * Persisted to the Python sidecar (which in turn writes to SQLite).
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { ModelInfo } from "../types/ipc";
import { strings } from "../ui/strings";

export type ModelEntry = ModelInfo;

export interface ModelState {
  models: ModelEntry[];
  current: string | null;
  /**
   * The persisted reasoning-effort override (R63).
   *
   * ``null`` / undefined means "no override — use the selected model's own
   * default effort" (the pre-R61 state). A non-null string is the canonical
   * wire token (``low`` / ``medium`` / ``high`` / ``xhigh``) the backend stored
   * via ``model.set_reasoning_effort`` (R61) and threads into the LLM call (R62).
   */
  reasoningEffort: string | null;
  loading: boolean;

  refresh: () => Promise<void>;
  setCurrent: (id: string) => Promise<void>;
  setReasoningEffort: (effort: string | null) => Promise<void>;
}

export const useModelStore = create<ModelState>((set) => ({
  models: [],
  current: null,
  reasoningEffort: null,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listModels();
      // R63: read back the persisted reasoning-effort override alongside the
      // model list so the badge switcher renders the current selection
      // (symmetric to the R61 backend write-side read-back).
      set({
        models: r.models,
        current: r.current,
        reasoningEffort: r.reasoning_effort ?? null,
        loading: false,
      });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.modelsLoadFailed, message);
    }
  },

  setCurrent: async (id: string) => {
    try {
      // Resolve the owning provider from the cached model list so the
      // switch routes to that provider's endpoint/key. Without this the
      // backend preference row could pin the model to the wrong provider
      // and every request would keep hitting the MiniMax quota even after
      // switching to a third-party model (v1.6.2 field report).
      const entry = useModelStore
        .getState()
        .models.find((m) => m.id === id);
      const r = await typedIPC.setCurrentModel(
        id,
        entry?.provider_id ?? undefined,
      );
      set({ current: r.current });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.modelSwitchFailed, message);
    }
  },

  setReasoningEffort: async (effort: string | null) => {
    try {
      const r = await typedIPC.setReasoningEffort(effort);
      set({ reasoningEffort: r.reasoning_effort });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.effortSetFailed, message);
    }
  },
}));
