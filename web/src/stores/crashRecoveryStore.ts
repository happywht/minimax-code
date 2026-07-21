/**
 * Crash-recovery store — drives the "your last session crashed" prompt.
 *
 * Backed by the ``crash.*`` IPC namespace (R231), which reads the persisted
 * human-readable report the boot-time write half (R225-R230) produced.
 *
 * The store owns three pieces of UI state:
 *
 *   - the *current* prompt (``available`` + ``reportText``) — fetched once at
 *     boot via ``loadPreviousReport``; cleared by ``dismiss``.
 *   - the *history* list (``history``) — fetched on demand via ``loadHistory``
 *     when the user opens the history modal.
 *   - the history modal's open/closed flag (``historyOpen``).
 *
 * Every action is fail-open at the IPC boundary: a flaky filesystem never
 * breaks the recovery UI — it surfaces a toast for diagnosability and leaves
 * ``available: false`` (the honest "nothing to show" steady state). This
 * mirrors the backend handler's fail-open contract.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import type { CrashHistoryEntry } from "../types/ipc";

export interface CrashRecoveryState {
  /** True when a previous-crash report exists and the prompt should show. */
  available: boolean;
  /** The full report text from ``last-crash-report.txt`` (null when absent). */
  reportText: string | null;
  /** True while ``loadPreviousReport`` is in flight. */
  loading: boolean;
  /** Archived past-crash entries (newest first, capped at 50 by the handler). */
  history: CrashHistoryEntry[];
  /** Whether the history modal is open. */
  historyOpen: boolean;

  /** Pull the current previous-crash report from the backend. */
  loadPreviousReport: () => Promise<void>;
  /** Pull the archived crash history from the backend. */
  loadHistory: () => Promise<void>;
  /** Ask the backend to delete the current report (hides the prompt). */
  dismiss: () => Promise<void>;
  /** Open the history modal and refresh the list. */
  openHistory: () => Promise<void>;
  /** Close the history modal. */
  closeHistory: () => void;
}

export const useCrashRecoveryStore = create<CrashRecoveryState>((set, get) => ({
  available: false,
  reportText: null,
  loading: false,
  history: [],
  historyOpen: false,

  loadPreviousReport: async () => {
    set({ loading: true });
    try {
      const result = await typedIPC.crashPreviousReport();
      set({
        available: result.available,
        reportText: result.report_text,
        loading: false,
      });
    } catch (err) {
      set({ loading: false });
      // Fail-open: no previous crash is the normal steady state. Surface a
      // toast for diagnosability but leave ``available: false`` so the UI
      // does not block on a missing report.
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to read crash report", message);
    }
  },

  loadHistory: async () => {
    try {
      const result = await typedIPC.crashHistory();
      set({ history: result.entries });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load crash history", message);
    }
  },

  dismiss: async () => {
    try {
      const result = await typedIPC.crashDismiss();
      if (result.dismissed) {
        // Hide the prompt and any open history modal; the archival record is
        // untouched, so ``loadHistory`` still lists past crashes next time.
        set({ available: false, reportText: null, historyOpen: false });
      } else {
        // Backend could not remove the report (permission / lock). Tell the
        // user so they know the prompt will reappear on next boot.
        toast.error(
          "Could not dismiss crash report",
          "The report file is locked or missing.",
        );
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to dismiss crash report", message);
    }
  },

  openHistory: async () => {
    set({ historyOpen: true });
    await get().loadHistory();
  },

  closeHistory: () => set({ historyOpen: false }),
}));
