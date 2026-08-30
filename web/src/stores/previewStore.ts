/**
 * Preview store — manages the live-preview panel state.
 *
 * Tracks the preview server URL, SSE connection status,
 * and provides a manual reload trigger. The preview URL
 * shares the Agent origin in production and is configurable via
 * ``VITE_PREVIEW_URL`` during development.
 *
 * v1.7.1 adds ``setRoot`` — the per-project re-root seam. The backend's
 * preview surface (static serving / health / SSE) follows the selected
 * project's ``root_path``; this store drives that switch and reloads the
 * iframe so the new root is visible immediately.
 */
import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import { strings } from "../ui/strings";

export interface PreviewState {
  /** Base URL of the preview server (e.g. "http://127.0.0.1:8766"). */
  url: string;
  /** Current file path displayed in the preview iframe. */
  filePath: string;
  /** Whether the SSE connection is active. */
  connected: boolean;
  /** Monotonic counter — incrementing triggers an iframe reload. */
  reloadCounter: number;
  /**
   * The project id the backend preview root is anchored to
   * (``null`` = process default). Echoed from the last
   * ``preview.set_root`` reply — informational + test seam.
   */
  rootProjectId: string | null;
  /** Absolute workspace path the preview currently serves (last reply). */
  rootWorkspace: string;

  // Actions
  setFilePath: (path: string) => void;
  setConnected: (v: boolean) => void;
  reload: () => void;
  /**
   * Re-root the backend preview surface at a project's root and reload.
   * Called on every project switch (see ``sessionStore`` wiring); a null /
   * omitted id falls back to the process default root.
   */
  setRoot: (projectId?: string | null) => Promise<void>;
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
  rootProjectId: null,
  rootWorkspace: "",

  setFilePath: (path) => set({ filePath: path }),
  setConnected: (v) => set({ connected: v }),
  reload: () => set((s) => ({ reloadCounter: s.reloadCounter + 1 })),

  setRoot: async (projectId) => {
    try {
      const r = await typedIPC.setPreviewRoot({
        project_id: projectId ?? undefined,
      });
      set({
        rootProjectId: r.project_id ?? null,
        rootWorkspace: r.workspace,
        reloadCounter: usePreviewStore.getState().reloadCounter + 1,
      });
    } catch (err) {
      // The preview panel stays on the previous root — the iframe keeps
      // rendering whatever it had; only surface why the switch failed.
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.previewRootFailed, message);
    }
  },
}));
