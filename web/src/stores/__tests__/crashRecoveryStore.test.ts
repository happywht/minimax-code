/**
 * Tests for the crash-recovery store (R232).
 *
 * The store is the bridge between the R231 ``crash.*`` IPC namespace and the
 * CrashRecoveryPrompt UI: it owns the current prompt (``available`` /
 * ``reportText``), the archived history list, and the history-modal flag.
 *
 * Both the IPC layer and the toast bus are stubbed with ``vi.hoisted`` so the
 * stubs exist before the hoisted ``vi.mock`` factories run. Each scenario
 * drives a single IPC method and asserts the resulting store state (and, for
 * the fail-open paths, the toast payload the user would see).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const stubs = vi.hoisted(() => ({
  crashPreviousReport: vi.fn(),
  crashHistory: vi.fn(),
  crashDismiss: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("../../components/layout/ErrorBoundary", () => ({
  toast: {
    error: (...args: unknown[]) => stubs.toastError(...args),
    success: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@/ipc/client", () => ({
  typedIPC: {
    crashPreviousReport: stubs.crashPreviousReport,
    crashHistory: stubs.crashHistory,
    crashDismiss: stubs.crashDismiss,
  },
  ipc: {
    on: () => () => {},
    off: () => {},
    start: () => Promise.resolve(),
    ping: () => Promise.resolve(true),
  },
  IPCError: class extends Error {},
}));

import { useCrashRecoveryStore } from "../crashRecoveryStore";
import type { CrashHistoryEntry } from "../../types/ipc";

function resetStore(): void {
  useCrashRecoveryStore.setState({
    available: false,
    reportText: null,
    loading: false,
    history: [],
    historyOpen: false,
  });
}

describe("crashRecoveryStore", () => {
  beforeEach(() => {
    stubs.toastError.mockClear();
    stubs.crashPreviousReport.mockReset();
    stubs.crashHistory.mockReset();
    stubs.crashDismiss.mockReset();
    resetStore();
  });

  describe("loadPreviousReport", () => {
    it("sets available + reportText when a report exists", async () => {
      stubs.crashPreviousReport.mockResolvedValue({
        available: true,
        report_text: "SIGBUS at 0xdeadbeef\n",
      });

      await useCrashRecoveryStore.getState().loadPreviousReport();

      const state = useCrashRecoveryStore.getState();
      expect(state.available).toBe(true);
      expect(state.reportText).toBe("SIGBUS at 0xdeadbeef\n");
      expect(state.loading).toBe(false);
      expect(stubs.toastError).not.toHaveBeenCalled();
    });

    it("keeps available false when no report exists (steady state)", async () => {
      stubs.crashPreviousReport.mockResolvedValue({
        available: false,
        report_text: null,
      });

      await useCrashRecoveryStore.getState().loadPreviousReport();

      const state = useCrashRecoveryStore.getState();
      expect(state.available).toBe(false);
      expect(state.reportText).toBeNull();
      expect(state.loading).toBe(false);
    });

    it("surfaces a toast and stays fail-open on IPC error", async () => {
      stubs.crashPreviousReport.mockRejectedValue(new Error("ipc-down"));

      await useCrashRecoveryStore.getState().loadPreviousReport();

      const state = useCrashRecoveryStore.getState();
      expect(state.available).toBe(false);
      expect(state.loading).toBe(false);
      expect(stubs.toastError).toHaveBeenCalledWith(
        "读取崩溃报告失败",
        "ipc-down",
      );
    });
  });

  describe("loadHistory", () => {
    it("stores the archived entries (newest first, as the backend returns)", async () => {
      const entries: CrashHistoryEntry[] = [
        { filename: "crash-3000.txt", timestamp: 3000, report_text: "c3" },
        { filename: "crash-2000.txt", timestamp: 2000, report_text: "c2" },
      ];
      stubs.crashHistory.mockResolvedValue({ entries });

      await useCrashRecoveryStore.getState().loadHistory();

      expect(useCrashRecoveryStore.getState().history).toEqual(entries);
    });

    it("surfaces a toast on IPC error", async () => {
      stubs.crashHistory.mockRejectedValue(new Error("ipc-down"));

      await useCrashRecoveryStore.getState().loadHistory();

      expect(stubs.toastError).toHaveBeenCalledWith(
        "加载崩溃历史失败",
        "ipc-down",
      );
    });
  });

  describe("dismiss", () => {
    it("clears the prompt and closes the modal when backend confirms", async () => {
      useCrashRecoveryStore.setState({
        available: true,
        reportText: "crash",
        historyOpen: true,
      });
      stubs.crashDismiss.mockResolvedValue({ dismissed: true });

      await useCrashRecoveryStore.getState().dismiss();

      const state = useCrashRecoveryStore.getState();
      expect(state.available).toBe(false);
      expect(state.reportText).toBeNull();
      expect(state.historyOpen).toBe(false);
      expect(stubs.toastError).not.toHaveBeenCalled();
    });

    it("keeps state and surfaces a toast when backend could not dismiss", async () => {
      useCrashRecoveryStore.setState({
        available: true,
        reportText: "crash",
        historyOpen: true,
      });
      stubs.crashDismiss.mockResolvedValue({ dismissed: false });

      await useCrashRecoveryStore.getState().dismiss();

      const state = useCrashRecoveryStore.getState();
      // State unchanged -- the prompt will reappear until the report is gone.
      expect(state.available).toBe(true);
      expect(state.reportText).toBe("crash");
      expect(stubs.toastError).toHaveBeenCalledWith(
        "Could not dismiss crash report",
        expect.any(String),
      );
    });

    it("surfaces a toast on IPC error", async () => {
      stubs.crashDismiss.mockRejectedValue(new Error("ipc-down"));

      await useCrashRecoveryStore.getState().dismiss();

      expect(stubs.toastError).toHaveBeenCalledWith(
        "忽略崩溃报告失败",
        "ipc-down",
      );
    });
  });

  describe("openHistory / closeHistory", () => {
    it("opens the modal and refreshes the history list", async () => {
      const entries: CrashHistoryEntry[] = [
        { filename: "crash-1000.txt", timestamp: 1000, report_text: "c1" },
      ];
      stubs.crashHistory.mockResolvedValue({ entries });

      await useCrashRecoveryStore.getState().openHistory();

      const state = useCrashRecoveryStore.getState();
      expect(state.historyOpen).toBe(true);
      expect(state.history).toEqual(entries);
    });

    it("closes the modal without touching the cached history", () => {
      useCrashRecoveryStore.setState({
        historyOpen: true,
        history: [
          { filename: "crash-1000.txt", timestamp: 1000, report_text: "c1" },
        ],
      });

      useCrashRecoveryStore.getState().closeHistory();

      const state = useCrashRecoveryStore.getState();
      expect(state.historyOpen).toBe(false);
      expect(state.history).toHaveLength(1);
    });
  });
});
