/**
 * GitViewerModal — full-screen-ish modal for browsing git diffs and logs.
 *
 * Two tabs:
 *   - **Diff** — renders a unified diff with colour-coded lines
 *     (green additions, red deletions, grey hunk headers).
 *   - **Log** — lists recent commits (short hash, author, message,
 *     files changed).
 *
 * The modal fetches data lazily on mount (and when switching tabs)
 * via the ``useGitStore`` actions. It owns no state beyond the
 * selected tab.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { X, FileText, GitCommit as CommitIcon } from "lucide-react";
import { SkeletonTable } from "./Skeleton";
import { useGitStore } from "../stores";
import { useFocusTrap } from "../lib/useFocusTrap";
import type { GitLogEntry } from "../types/ipc";

export interface GitViewerModalProps {
  open: boolean;
  onClose: () => void;
  /** Which tab is active when the modal opens. */
  initialTab?: "diff" | "log";
  testId?: string;
}

export function GitViewerModal({
  open,
  onClose,
  initialTab = "diff",
  testId = "git-viewer-modal",
}: GitViewerModalProps): JSX.Element | null {
  const [tab, setTab] = useState<"diff" | "log">(initialTab);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, open);

  const lastDiff = useGitStore((s) => s.lastDiff);
  const lastLog = useGitStore((s) => s.lastLog);
  const loading = useGitStore((s) => s.loading);
  const fetchDiff = useGitStore((s) => s.fetchDiff);
  const fetchLog = useGitStore((s) => s.fetchLog);

  // Fetch data when modal opens or tab changes.
  useEffect(() => {
    if (!open) return;
    if (tab === "diff") {
      void fetchDiff({ scope: "working" });
    } else {
      void fetchLog({ n: 20 });
    }
  }, [open, tab, fetchDiff, fetchLog]);

  const handleTabChange = useCallback((t: "diff" | "log") => {
    setTab(t);
  }, []);

  if (!open) return null;

  return (
    <div
      ref={dialogRef}
      data-testid={testId}
      role="dialog"
      aria-modal="true"
      aria-label="Git viewer"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[80vh] w-[720px] flex-col rounded-lg border border-minimax-border bg-minimax-panel shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-minimax-border px-4 py-3">
          <div className="flex gap-1">
            <button
              type="button"
              data-testid="git-viewer-tab-diff"
              onClick={() => handleTabChange("diff")}
              className={
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors " +
                (tab === "diff"
                  ? "bg-minimax-accent/20 text-minimax-accent"
                  : "text-minimax-muted hover:text-minimax-fg")
              }
            >
              <FileText size={12} />
              Diff
            </button>
            <button
              type="button"
              data-testid="git-viewer-tab-log"
              onClick={() => handleTabChange("log")}
              className={
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors " +
                (tab === "log"
                  ? "bg-minimax-accent/20 text-minimax-accent"
                  : "text-minimax-muted hover:text-minimax-fg")
              }
            >
              <CommitIcon size={12} />
              Log
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close git viewer"
            className="rounded p-1 text-minimax-muted hover:text-minimax-fg"
          >
            <X size={14} />
          </button>
        </div>

        {/* Content */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && !lastDiff && !lastLog ? (
            <div className="p-4">
              <SkeletonTable rows={6} />
            </div>
          ) : tab === "diff" ? (
            <DiffContent
              text={lastDiff?.diff ?? null}
              testId={`${testId}-diff-content`}
            />
          ) : (
            <LogContent
              entries={lastLog?.entries ?? null}
              testId={`${testId}-log-content`}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diff renderer — colour-coded unified diff lines
// ---------------------------------------------------------------------------

function DiffContent({
  text,
  testId,
}: {
  text: string | null;
  testId: string;
}): JSX.Element {
  if (!text) {
    return (
      <div className="px-4 py-8 text-center text-xs text-minimax-muted">
        No diff available.
      </div>
    );
  }

  const lines = text.split("\n");

  return (
    <pre
      data-testid={testId}
      className="px-4 py-3 font-mono text-[11px] leading-relaxed"
    >
      {lines.map((line, i) => {
        let cls = "text-minimax-fg";
        if (line.startsWith("+++") || line.startsWith("---")) {
          cls = "font-bold text-minimax-accent";
        } else if (line.startsWith("@@")) {
          cls = "text-minimax-muted";
        } else if (line.startsWith("+")) {
          cls = "text-status-success";
        } else if (line.startsWith("-")) {
          cls = "text-status-error";
        }
        return (
          <div key={i} className={cls}>
            {line}
          </div>
        );
      })}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Log renderer — commit list
// ---------------------------------------------------------------------------

function LogContent({
  entries,
  testId,
}: {
  entries: GitLogEntry[] | null;
  testId: string;
}): JSX.Element {
  if (!entries || entries.length === 0) {
    return (
      <div className="px-4 py-8 text-center text-xs text-minimax-muted">
        No commits found.
      </div>
    );
  }

  return (
    <ul data-testid={testId} className="divide-y divide-minimax-border">
      {entries.map((c) => (
        <li
          key={c.sha}
          className="flex items-start gap-3 px-4 py-2.5 hover:bg-minimax-border/30"
        >
          <span className="mt-0.5 shrink-0 font-mono text-[11px] text-minimax-accent">
            {c.sha.slice(0, 7)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs text-minimax-fg">
              {c.message}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-minimax-muted">
              <span>{c.author}</span>
              {c.files_changed != null && c.files_changed.length > 0 ? (
                <span>{c.files_changed.length} files</span>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
