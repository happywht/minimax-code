/**
 * Preview store — manages the live-preview panel state.
 *
 * Tracks the preview server URL, SSE connection status,
 * and provides a manual reload trigger. The preview URL
 * shares the Agent origin in production and is configurable via
 * ``VITE_PREVIEW_URL`` during development.
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

function defaultPreviewUrl(): string {
  const configured = import.meta.env.VITE_PREVIEW_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (import.meta.env.DEV) {
    return (import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");
  }
  if (typeof window !== "undefined" && window.location.origin) {
    return window.location.origin;
  }
  return "http://127.0.0.1:8765";
}

export const usePreviewStore = create<PreviewState>((set) => ({
  url: defaultPreviewUrl(),
  filePath: "index.html",
  connected: false,
  reloadCounter: 0,

  setFilePath: (path) => set({ filePath: path }),
  setConnected: (v) => set({ connected: v }),
  reload: () => set((s) => ({ reloadCounter: s.reloadCounter + 1 })),
}));
