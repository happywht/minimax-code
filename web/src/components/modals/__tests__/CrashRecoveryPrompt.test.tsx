/**
 * Tests for the CrashRecoveryPrompt component (R232).
 *
 * The component is the terminal UI of the crash module: it renders a fixed
 * banner when a previous-crash report exists, with "History" and "Dismiss"
 * actions, and an inline modal listing the archived crashes. The store is
 * stubbed via ``vi.hoisted`` so each test can drive a deterministic snapshot
 * without touching the real Zustand store (or the IPC layer behind it).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { strings } from "../../../ui/strings";

type HistoryEntry = {
  filename: string;
  timestamp: number;
  report_text: string;
};

const storeStub = vi.hoisted(() => ({
  available: false,
  reportText: null as string | null,
  history: [] as HistoryEntry[],
  historyOpen: false,
  dismiss: vi.fn(),
  openHistory: vi.fn(),
  closeHistory: vi.fn(),
}));

vi.mock("../../../stores", () => ({
  useCrashRecoveryStore: <T,>(selector: (s: typeof storeStub) => T): T =>
    selector(storeStub),
}));

import { CrashRecoveryPrompt } from "../CrashRecoveryPrompt";

describe("CrashRecoveryPrompt", () => {
  beforeEach(() => {
    storeStub.available = false;
    storeStub.reportText = null;
    storeStub.history = [];
    storeStub.historyOpen = false;
    storeStub.dismiss.mockClear();
    storeStub.openHistory.mockClear();
    storeStub.closeHistory.mockClear();
  });

  it("renders nothing when no previous crash is available (steady state)", () => {
    storeStub.available = false;
    storeStub.reportText = null;

    render(<CrashRecoveryPrompt />);

    expect(screen.queryByTestId("crash-recovery-prompt")).not.toBeInTheDocument();
    expect(screen.queryByTestId("crash-history-modal")).not.toBeInTheDocument();
  });

  it("renders the banner with the full report when a crash is available", () => {
    storeStub.available = true;
    storeStub.reportText = "SIGBUS at 0xdeadbeef\nstack trace follows";

    render(<CrashRecoveryPrompt />);

    expect(screen.getByTestId("crash-recovery-prompt")).toBeInTheDocument();
    expect(screen.getByText("上次会话异常退出")).toBeInTheDocument();
    const preview = screen.getByTestId("crash-recovery-report-preview");
    expect(preview.textContent).toBe("SIGBUS at 0xdeadbeef\nstack trace follows");
  });

  it("truncates a long report with an ellipsis", () => {
    storeStub.available = true;
    storeStub.reportText = "X".repeat(300);

    render(<CrashRecoveryPrompt />);

    const preview = screen.getByTestId("crash-recovery-report-preview");
    expect(preview.textContent).toBe("X".repeat(240) + "…");
  });

  it("invokes dismiss when the Dismiss button is clicked", () => {
    storeStub.available = true;
    storeStub.reportText = "boom";

    render(<CrashRecoveryPrompt />);

    fireEvent.click(screen.getByTestId("crash-recovery-dismiss"));
    expect(storeStub.dismiss).toHaveBeenCalledTimes(1);
  });

  it("invokes openHistory when the History button is clicked", () => {
    storeStub.available = true;
    storeStub.reportText = "boom";

    render(<CrashRecoveryPrompt />);

    fireEvent.click(screen.getByTestId("crash-recovery-history"));
    expect(storeStub.openHistory).toHaveBeenCalledTimes(1);
  });

  it("renders the history modal with entries when historyOpen", () => {
    storeStub.available = true;
    storeStub.reportText = "boom";
    storeStub.historyOpen = true;
    storeStub.history = [
      { filename: "crash-1000.txt", timestamp: 1000, report_text: "old crash" },
    ];

    render(<CrashRecoveryPrompt />);

    expect(screen.getByTestId("crash-history-modal")).toBeInTheDocument();
    expect(
      screen.getByTestId("crash-history-entry-crash-1000.txt"),
    ).toBeInTheDocument();
    expect(screen.getByText("old crash")).toBeInTheDocument();
  });

  it("shows an empty-state message when the history list is empty", () => {
    storeStub.available = true;
    storeStub.reportText = "boom";
    storeStub.historyOpen = true;
    storeStub.history = [];

    render(<CrashRecoveryPrompt />);

    expect(screen.getByText(strings.modals.crash.historyEmpty)).toBeInTheDocument();
  });

  it("closes the history modal via the close button", () => {
    storeStub.available = true;
    storeStub.reportText = "boom";
    storeStub.historyOpen = true;

    render(<CrashRecoveryPrompt />);

    // The history modal is built on the shared Modal primitive; its
    // header close button is `${testId}-close`.
    fireEvent.click(screen.getByTestId("crash-history-modal-close"));
    expect(storeStub.closeHistory).toHaveBeenCalledTimes(1);
  });
});
