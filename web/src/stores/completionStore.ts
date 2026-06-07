/**
 * Zustand store for inline code completion.
 *
 * Calls POST /complete directly (not via TypedIPC) since the completion
 * endpoint is a standalone FastAPI route, not an IPC method.
 */
import { create } from "zustand";
import type { CompletionRequest, CompletionResponse } from "../types/ipc";

const AGENT_URL =
  import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8765";

export interface CompletionState {
  loading: boolean;
  lastResult: CompletionResponse | null;
  error: string | null;
  complete: (req: CompletionRequest) => Promise<CompletionResponse>;
}

export const useCompletionStore = create<CompletionState>((set) => ({
  loading: false,
  lastResult: null,
  error: null,

  complete: async (req: CompletionRequest) => {
    set({ loading: true, error: null });
    try {
      const resp = await fetch(`${AGENT_URL}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error ?? `HTTP ${resp.status}`);
      }
      const data: CompletionResponse = await resp.json();
      set({ loading: false, lastResult: data });
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: msg });
      throw err;
    }
  },
}));
