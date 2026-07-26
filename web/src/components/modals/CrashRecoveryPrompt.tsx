/**
 * CrashRecoveryPrompt — the terminal UI surface of the crash-recovery module.
 *
 * Renders a fixed banner (bottom-center) when the previous session left a
 * crash report (``available: true`` + ``reportText`` from R231's
 * ``crash.previous_report``). The banner shows a truncated preview of the
 * report plus two actions:
 *
 *   - "History" -> opens a modal listing the archived past crashes
 *     (``crash.history``); the modal refreshes its list each time it opens.
 *   - "Dismiss" -> asks the backend to delete the current report
 *     (``crash.dismiss``); on success the prompt hides until the next crash.
 *
 * When there is no previous crash (the normal steady state) the component
 * renders nothing, so a healthy boot is visually unchanged. This closes the
 * crash module's loop end-to-end: write half (R225-R230) -> IPC (R231) ->
 * user-facing prompt (this component).
 */

import { AlertOctagon, History } from "lucide-react";

import { useCrashRecoveryStore } from "../../stores";
import { Button, Modal } from "../../ui";
import type { CrashHistoryEntry } from "../../types/ipc";

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
      className="rounded-md border border-line bg-surface-2 p-2"
    >
      <div className="mb-1 flex items-center justify-between text-[11px] text-ink-2">
        <span className="font-mono">{entry.filename}</span>
        <time>{formatTimestamp(entry.timestamp)}</time>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-0/60 px-2 py-1 font-mono text-[11px] text-ink-1">
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
        className="pointer-events-auto fixed inset-x-3 bottom-3 z-40 mx-auto max-w-2xl animate-rise-in rounded-lg border border-status-warning/40 bg-surface-1/95 px-3 py-2 text-xs shadow-pop backdrop-blur"
        role="alertdialog"
        aria-label="Previous session crashed"
      >
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--status-warning-subtle)] text-status-warning">
            <AlertOctagon size={15} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-medium text-ink-0">Your last session crashed</div>
            <pre
              data-testid="crash-recovery-report-preview"
              className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-0/60 px-2 py-1 font-mono text-[11px] text-ink-1"
            >
              {preview}
            </pre>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              icon={<History />}
              data-testid="crash-recovery-history"
              onClick={() => void openHistory()}
            >
              History
            </Button>
            <Button
              variant="secondary"
              size="sm"
              data-testid="crash-recovery-dismiss"
              onClick={() => void dismiss()}
            >
              Dismiss
            </Button>
          </div>
        </div>
      </div>
      {historyOpen && (
        <Modal
          testId="crash-history-modal"
          title="Crash history"
          onClose={closeHistory}
          widthClass="max-w-2xl"
        >
          {history.length === 0 ? (
            <div className="px-2 py-8 text-center text-ink-2">
              No crash history.
            </div>
          ) : (
            <ul className="space-y-2">
              {history.map((entry) => (
                <HistoryEntryRow key={entry.filename} entry={entry} />
              ))}
            </ul>
          )}
        </Modal>
      )}
    </>
  );
}
