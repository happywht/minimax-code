/**
 * CrashRecoveryPrompt — the terminal UI surface of the crash-recovery module.
 *
 * Renders a fixed banner (bottom-center) when the previous session left a
 * crash report (``available: true`` + ``reportText`` from R231's
 * ``crash.previous_report``). The banner shows a truncated preview of the
 * report plus two actions:
 *
 *   - "History" -> opens an inline modal listing the archived past crashes
 *     (``crash.history``); the modal refreshes its list each time it opens.
 *   - "Dismiss" -> asks the backend to delete the current report
 *     (``crash.dismiss``); on success the prompt hides until the next crash.
 *
 * When there is no previous crash (the normal steady state) the component
 * renders nothing, so a healthy boot is visually unchanged. This closes the
 * crash module's loop end-to-end: write half (R225-R230) -> IPC (R231) ->
 * user-facing prompt (this component).
 */

import { AlertOctagon, History, X } from "lucide-react";

import { useCrashRecoveryStore } from "../stores";
import type { CrashHistoryEntry } from "../types/ipc";

/** Truncate the report preview so a long stack trace does not flood the bar. */
const REPORT_PREVIEW_MAX = 240;

function formatTimestamp(epochSeconds: number): string {
  // ISO string keeps the test deterministic (no locale clock); the backend
  // timestamp is epoch-seconds, matching R225's ``crash-<ts>.txt`` filename.
  return new Date(epochSeconds * 1000).toISOString();
}

function HistoryEntryRow({ entry }: { entry: CrashHistoryEntry }): JSX.Element {
  return (
    <li
      data-testid={`crash-history-entry-${entry.filename}`}
      className="rounded-md border border-minimax-border bg-black/20 p-2"
    >
      <div className="mb-1 flex items-center justify-between text-[11px] text-minimax-muted">
        <span className="font-mono">{entry.filename}</span>
        <time>{formatTimestamp(entry.timestamp)}</time>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-minimax-muted">
        {entry.report_text}
      </pre>
    </li>
  );
}

export function CrashRecoveryPrompt(): JSX.Element | null {
  const available = useCrashRecoveryStore((s) => s.available);
  const reportText = useCrashRecoveryStore((s) => s.reportText);
  const history = useCrashRecoveryStore((s) => s.history);
  const historyOpen = useCrashRecoveryStore((s) => s.historyOpen);
  const dismiss = useCrashRecoveryStore((s) => s.dismiss);
  const openHistory = useCrashRecoveryStore((s) => s.openHistory);
  const closeHistory = useCrashRecoveryStore((s) => s.closeHistory);

  if (!available || reportText === null) {
    // No previous crash — the normal steady state. Render nothing so a
    // healthy boot is visually unchanged.
    return null;
  }

  const preview =
    reportText.length > REPORT_PREVIEW_MAX
      ? reportText.slice(0, REPORT_PREVIEW_MAX) + "…"
      : reportText;

  return (
    <>
      <div
        data-testid="crash-recovery-prompt"
        className="pointer-events-auto fixed inset-x-3 bottom-3 z-40 mx-auto max-w-2xl rounded-lg border border-amber-500/40 bg-minimax-panel/95 px-3 py-2 text-xs shadow-2xl backdrop-blur animate-in slide-in-from-bottom-2 fade-in duration-200"
        role="alertdialog"
        aria-label="Previous session crashed"
      >
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-amber-500/10 text-amber-300">
            <AlertOctagon size={15} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-medium text-minimax-fg">Your last session crashed</div>
            <pre
              data-testid="crash-recovery-report-preview"
              className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-minimax-muted"
            >
              {preview}
            </pre>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              data-testid="crash-recovery-history"
              onClick={() => void openHistory()}
              className="inline-flex h-7 items-center gap-1 rounded-md border border-minimax-border px-2 font-medium text-minimax-fg transition-colors duration-200 hover:bg-minimax-border"
            >
              <History size={12} />
              History
            </button>
            <button
              type="button"
              data-testid="crash-recovery-dismiss"
              onClick={() => void dismiss()}
              className="inline-flex h-7 items-center rounded-md border border-minimax-border px-2 font-medium text-minimax-fg transition-colors duration-200 hover:bg-minimax-border"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
      {historyOpen && (
        <div
          data-testid="crash-history-modal"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Crash history"
        >
          <div className="relative flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-minimax-border bg-minimax-panel shadow-2xl">
            <div className="flex items-center justify-between border-b border-minimax-border px-4 py-2">
              <div className="font-medium text-minimax-fg">Crash history</div>
              <button
                type="button"
                aria-label="Close history"
                onClick={closeHistory}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
              >
                <X size={14} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-2">
              {history.length === 0 ? (
                <div className="px-2 py-8 text-center text-minimax-muted">
                  No crash history.
                </div>
              ) : (
                <ul className="space-y-2">
                  {history.map((entry) => (
                    <HistoryEntryRow key={entry.filename} entry={entry} />
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
