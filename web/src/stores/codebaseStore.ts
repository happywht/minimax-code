/**
 * Codebase store — UI state for the Codebase side panel.
 *
 * Wraps the ``codebase.*`` IPC namespace introduced in v0.11.0 and
 * exposes a small polling refresh so the panel progress bar updates
 * while indexing runs in the background.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type {
  CodebaseSearchResult,
  CodebaseStatusResult,
  CodebaseSummarizeResult,
} from "../types/ipc";

export interface CodebaseState {
  /** Latest status snapshot — ``null`` until first refresh. */
  status: CodebaseStatusResult | null;
  /** True while ``buildIndex`` or ``refreshStatus`` is in flight. */
  loading: boolean;

  /** Current search query owned by the panel input. */
  query: string;
  /** True while a search request is in flight. */
  searching: boolean;
  /** Results from the latest successful search. */
  results: CodebaseSearchResult[];

  /** Path input for the summarize box. */
  summaryPath: string;
  /** Result of the latest summarize call. */
  summary: CodebaseSummarizeResult | null;
  /** True while summarize is in flight. */
  summarizing: boolean;

  /** Derived top files — counted from the current search results. */
  hotFiles: { file_path: string; count: number }[];
  /** Recently viewed / searched file paths (most recent first). */
  recentFiles: string[];

  /**
   * Poll ``codebase.status``. Safe to call repeatedly; failures are
   * silent so the panel never spams toasts on a timer.
   */
  refreshStatus: () => Promise<void>;
  /**
   * Trigger ``codebase.build_index``. The returned status is merged
   * into state; polling will pick up the live progress.
   */
  buildIndex: (force?: boolean) => Promise<void>;

  /** Update the search query input without running a search. */
  setQuery: (q: string) => void;
  /** Run ``codebase.search`` for the current query. */
  search: (q?: string) => Promise<void>;

  /** Update the summarize path input. */
  setSummaryPath: (p: string) => void;
  /** Run ``codebase.summarize`` for the current path. */
  summarize: (p?: string) => Promise<void>;

  /** Record that a file was interacted with so it appears in recent files. */
  touchFile: (filePath: string) => void;

  /** Drop cached state (e.g. on workspace switch). */
  reset: () => void;
}

const POLL_INTERVAL_MS = 2000;

function computeHotFiles(results: CodebaseSearchResult[]): CodebaseState["hotFiles"] {
  const counts = new Map<string, number>();
  for (const r of results) {
    counts.set(r.file_path, (counts.get(r.file_path) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([file_path, count]) => ({ file_path, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);
}

export const useCodebaseStore = create<CodebaseState>((set, get) => ({
  status: null,
  loading: false,
  query: "",
  searching: false,
  results: [],
  summaryPath: "",
  summary: null,
  summarizing: false,
  hotFiles: [],
  recentFiles: [],

  refreshStatus: async () => {
    set({ loading: true });
    try {
      const s = await typedIPC.getCodebaseStatus();
      set({ status: s, loading: false });
    } catch {
      set({ loading: false });
      // Silent on poll failures.
    }
  },

  buildIndex: async (force = true) => {
    set({ loading: true });
    try {
      const s = await typedIPC.buildCodebaseIndex({ force });
      set({ status: s, loading: false });
      toast.success("Codebase index started");
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to build codebase index", message);
    }
  },

  setQuery: (q) => set({ query: q }),

  search: async (q) => {
    const query = (q ?? get().query).trim();
    if (!query) {
      set({ results: [], hotFiles: [] });
      return;
    }
    set({ searching: true, query });
    try {
      const res = await typedIPC.searchCodebase(query);
      const hotFiles = computeHotFiles(res.results);
      set({ results: res.results, hotFiles, searching: false });
      // Record every distinct matched file as recently touched.
      const touched = new Set(res.results.map((r) => r.file_path));
      const current = get().recentFiles.filter((f) => !touched.has(f));
      set({ recentFiles: [...touched, ...current].slice(0, 10) });
    } catch (err) {
      set({ searching: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Search failed", message);
    }
  },

  setSummaryPath: (p) => set({ summaryPath: p }),

  summarize: async (p) => {
    const path = (p ?? get().summaryPath).trim();
    if (!path) {
      set({ summary: null });
      return;
    }
    set({ summarizing: true, summaryPath: path });
    try {
      const s = await typedIPC.summarizeCodebasePath(path);
      set({ summary: s, summarizing: false });
      get().touchFile(path);
    } catch (err) {
      set({ summarizing: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Summarize failed", message);
    }
  },

  touchFile: (filePath) => {
    set((state) => {
      const next = [filePath, ...state.recentFiles.filter((f) => f !== filePath)].slice(0, 10);
      return { recentFiles: next };
    });
  },

  reset: () =>
    set({
      status: null,
      loading: false,
      query: "",
      searching: false,
      results: [],
      summaryPath: "",
      summary: null,
      summarizing: false,
      hotFiles: [],
      recentFiles: [],
    }),
}));

/**
 * Start a background status poller for the codebase panel.
 * Returns an unsubscribe function.
 */
export function startCodebaseStatusPoller(): () => void {
  const store = useCodebaseStore.getState();
  void store.refreshStatus();
  const id = window.setInterval(() => {
    void useCodebaseStore.getState().refreshStatus();
  }, POLL_INTERVAL_MS);
  return () => window.clearInterval(id);
}
