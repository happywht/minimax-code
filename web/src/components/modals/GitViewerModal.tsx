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
 * selected tab. Shell (backdrop, Esc, focus trap, header) comes from
 * the shared `Modal` primitive; the tab switcher sits in its title slot.
 */

import { useCallback, useEffect, useState } from "react";
import { FileText, GitCommit as CommitIcon } from "lucide-react";
import { SkeletonTable } from "../layout/Skeleton";
import { useGitStore } from "../../stores";
import { Modal } from "../../ui";
import { strings } from "../../ui/strings";
import type { GitLogEntry } from "../../types/ipc";

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

  const tabButton = (key: "diff" | "log", label: string, icon: JSX.Element) => (
    <button
      type="button"
      data-testid={`git-viewer-tab-${key}`}
      onClick={() => handleTabChange(key)}
      className={
        "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors duration-150 " +
        (tab === key
          ? "bg-accent-subtle text-accent"
          : "text-ink-1 hover:bg-surface-3 hover:text-ink-0")
      }
    >
      {icon}
      {label}
    </button>
  );

  return (
    <Modal
      testId={testId}
      onClose={onClose}
      widthClass="max-w-3xl"
      title={
        <span className="flex items-center gap-1">
          <span className="sr-only">{strings.modals.gitViewer.srTitle}</span>
          {tabButton("diff", strings.modals.gitViewer.tabDiff, <FileText size={12} />)}
          {tabButton("log", strings.modals.gitViewer.tabLog, <CommitIcon size={12} />)}
        </span>
      }
    >
      {loading && !lastDiff && !lastLog ? (
        <SkeletonTable rows={6} />
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
    </Modal>
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
      <div className="px-1 py-6 text-center text-xs text-ink-2">
        {strings.modals.gitViewer.noDiff}
      </div>
    );
  }

  const lines = text.split("\n");

  return (
    <pre
      data-testid={testId}
      className="font-mono text-[11px] leading-relaxed"
    >
      {lines.map((line, i) => {
        let cls = "text-ink-1";
        if (line.startsWith("+++") || line.startsWith("---")) {
          cls = "font-semibold text-accent";
        } else if (line.startsWith("@@")) {
          cls = "text-status-info";
        } else if (line.startsWith("+")) {
          cls = "bg-[var(--status-success-subtle)] text-status-success";
        } else if (line.startsWith("-")) {
          cls = "bg-[var(--status-error-subtle)] text-status-error";
        }
        return (
          <div key={i} className={"px-1 " + cls}>
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
      <div className="px-1 py-6 text-center text-xs text-ink-2">
        {strings.modals.gitViewer.noCommits}
      </div>
    );
  }

  return (
    <ul data-testid={testId} className="divide-y divide-line">
      {entries.map((c) => (
        <li
          key={c.sha}
          className="flex items-start gap-3 px-2 py-2.5 transition-colors duration-150 hover:bg-surface-3"
        >
          <span className="mt-0.5 shrink-0 font-mono text-[11px] text-accent">
            {c.sha.slice(0, 7)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs text-ink-0">
              {c.message}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-2">
              <span>{c.author}</span>
              {c.files_changed != null && c.files_changed.length > 0 ? (
                <span>{strings.modals.fileCount(c.files_changed.length)}</span>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
