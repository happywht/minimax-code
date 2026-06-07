/**
 * Preview store — manages the live-preview panel state.
 *
 * Tracks the preview server URL, SSE connection status,
 * and provides a manual reload trigger. The preview URL
 * defaults to ``http://127.0.0.1:8766`` and is configurable
 * via ``VITE_PREVIEW_URL``.
 */
import { create } from "zustand";

export interface PreviewState {
  /** Base URL of the preview server (e.g. "http://127.0.0.1:8766"). */
  url: string;
  /** Current file path displayed in the preview iframe. */
  filePath: string;
  /** Whether the SSE connection is active. */
  connected: boolean;
  /** Monotonic counter — incrementing triggers an iframe reload. */
  reloadCounter: number;

  // Actions
  setFilePath: (path: string) => void;
  setConnected: (v: boolean) => void;
  reload: () => void;
}

const DEFAULT_URL =
  import.meta.env.VITE_PREVIEW_URL ?? "http://127.0.0.1:8766";

export const usePreviewStore = create<PreviewState>((set) => ({
  url: DEFAULT_URL,
  filePath: "index.html",
  connected: false,
  reloadCounter: 0,

  setFilePath: (path) => set({ filePath: path }),
  setConnected: (v) => set({ connected: v }),
  reload: () => set((s) => ({ reloadCounter: s.reloadCounter + 1 })),
}));
