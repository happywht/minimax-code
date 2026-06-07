/**
 * GitStatusBar — slim top-bar widget showing the current branch
 * and a dirty/clean indicator. Click to open a popover with the
 * full modified-file list.
 *
 * Visual layout (matches the v0.2.0 "top bar" design language):
 *
 *   ┌─────────────────────────┐
 *   │ ◉ main  ● (clean)      │  <- trigger button
 *   └─────────────────────────┘
 *         ↓ on click
 *   ┌─────────────────────────┐
 *   │ main                    │  <- branch header
 *   │ ─────                   │
 *   │ Modified (3)            │  <- dirty file list
 *   │   src/app.ts            │
 *   │   README.md             │
 *   │   …                     │
 *   │ Untracked (1)           │
 *   │   scratch.txt           │
 *   │ Staged (2)              │
 *   │   …                     │
 *   └─────────────────────────┘
 *
 * The widget is read-only — it shows what git says, but does
 * not stage / unstage / commit. Those actions stay on the user's
 * shell. v0.3.0 keeps the surface deliberately small.
 *
 * The status is polled on a short interval (``POLL_INTERVAL_MS``)
 * so a long-running agent that touches the working tree picks
 * up the change without forcing the user to refresh. The poll is
 * paused while the popover is open and the user is reading the
 * list — opening the popover is itself a refresh.
 */
import { useEffect, useRef, useState } from "react";
import { GitBranch as Branch, CheckCircle2, CircleAlert, FileText, Loader2, FileDiff, GitCommit as CommitIcon } from "lucide-react";
import { useGitStore } from "../stores";
import { GitViewerModal } from "./GitViewerModal";

const POLL_INTERVAL_MS = 15_000;

export interface GitStatusBarProps {
  testId?: string;
}

export function GitStatusBar({
  testId = "git-status-bar",
}: GitStatusBarProps): JSX.Element {
  const status = useGitStore((s) => s.status);
  const loading = useGitStore((s) => s.loading);
  const refreshStatus = useGitStore((s) => s.refreshStatus);
  const [open, setOpen] = useState(false);
  const [showViewer, setShowViewer] = useState(false);
  const [viewerTab, setViewerTab] = useState<"diff" | "log">("diff");
  const containerRef = useRef<HTMLDivElement>(null);

  // Initial fetch on mount + light polling so the indicator
  // updates as the user (or the agent) edits files.
  useEffect(() => {
    void refreshStatus();
    const id = setInterval(() => {
      // Skip the poll while the popover is open — the user is
      // actively reading the list and a refetch mid-read is
      // pointless (and can race with the click).
      if (open) return;
      void refreshStatus();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshStatus, open]);

  // Close on outside click — same pattern as ModelSelector.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  // Opening the popover always triggers a fresh fetch so the
  // file list is up to date by the time the user reads it.
  const onOpen = () => {
    setOpen((v) => !v);
    if (!open) {
      void refreshStatus();
    }
  };

  // Render a short status string for the trigger. The popover
  // shows the full list.
  const branch = status?.branch ?? "…";
  const clean = status?.clean ?? null;
  const totalChanges =
    (status?.modified?.length ?? 0) +
    (status?.untracked?.length ?? 0) +
    (status?.staged?.length ?? 0);
  const indicatorLabel =
    clean === null
      ? "loading…"
      : clean
        ? "clean"
        : `${totalChanges} change${totalChanges === 1 ? "" : "s"}`;

  return (
    <div ref={containerRef} className="relative" data-testid={testId}>
      <button
        type="button"
        onClick={onOpen}
        data-testid="git-status-bar-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={
          status
            ? `Branch ${branch} — ${indicatorLabel}`
            : "Loading git status…"
        }
        className="flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel/60 px-2.5 py-1 text-xs text-minimax-fg hover:border-minimax-accent/50"
      >
        <Branch size={12} className="text-minimax-muted" />
        <span data-testid="git-status-bar-branch" className="font-mono">
          {branch}
        </span>
        <span aria-hidden="true" className="text-minimax-muted">
          ·
        </span>
        <span
          data-testid="git-status-bar-indicator"
          className={
            "flex items-center gap-1 " +
            (clean === null
              ? "text-minimax-muted"
              : clean
                ? "text-emerald-400"
                : "text-amber-400")
          }
        >
          {clean === null ? (
            <Loader2
              size={10}
              className="animate-spin text-minimax-muted"
              data-testid="git-status-bar-loading"
            />
          ) : clean ? (
            <CheckCircle2 size={10} data-testid="git-status-bar-icon-clean" />
          ) : (
            <CircleAlert size={10} data-testid="git-status-bar-icon-dirty" />
          )}
          <span>{indicatorLabel}</span>
          {loading && clean !== null ? (
            <Loader2
              size={10}
              className="animate-spin text-minimax-muted"
              aria-hidden="true"
            />
          ) : null}
        </span>
      </button>
      {open ? (
        <div
          data-testid="git-status-bar-popover"
          role="dialog"
          aria-label={`Git status on ${branch}`}
          className="absolute left-0 top-full z-30 mt-1 w-72 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-xl"
        >
          <div
            data-testid="git-status-bar-popover-header"
            className="flex items-center gap-2 border-b border-minimax-border px-3 py-2 text-xs text-minimax-fg"
          >
            <Branch size={12} className="text-minimax-muted" />
            <span className="font-mono">{branch}</span>
            {status && status.ahead > 0 ? (
              <span
                data-testid="git-status-bar-ahead"
                className="ml-auto rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] text-emerald-300"
              >
                ↑ {status.ahead}
              </span>
            ) : null}
            {status && status.behind > 0 ? (
              <span
                data-testid="git-status-bar-behind"
                className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300"
              >
                ↓ {status.behind}
              </span>
            ) : null}
          </div>
          <div className="max-h-72 overflow-y-auto px-1 py-1 text-xs">
            <FileBucket
              label="Modified"
              testId="git-status-bar-modified"
              paths={status?.modified ?? []}
              empty={clean === true ? "no modified files" : null}
            />
            <FileBucket
              label="Staged"
              testId="git-status-bar-staged"
              paths={status?.staged ?? []}
              empty={clean === true ? "no staged files" : null}
            />
            <FileBucket
              label="Untracked"
              testId="git-status-bar-untracked"
              paths={status?.untracked ?? []}
              empty={clean === true ? "no untracked files" : null}
            />
            {status === null ? (
              <div
                data-testid="git-status-bar-empty"
                className="px-3 py-2 text-minimax-muted"
              >
                Loading git status…
              </div>
            ) : clean ? (
              <div
                data-testid="git-status-bar-clean-message"
                className="px-3 py-2 text-minimax-muted"
              >
                Working tree clean.
              </div>
            ) : null}
          </div>
          {/* Viewer trigger buttons */}
          <div className="flex gap-1 border-t border-minimax-border px-2 py-1.5">
            <button
              type="button"
              data-testid="git-status-bar-view-diff"
              onClick={() => { setViewerTab("diff"); setShowViewer(true); }}
              className="flex flex-1 items-center justify-center gap-1 rounded border border-minimax-border px-2 py-1 text-[11px] text-minimax-muted hover:text-minimax-fg"
            >
              <FileDiff size={10} />
              View Diff
            </button>
            <button
              type="button"
              data-testid="git-status-bar-view-log"
              onClick={() => { setViewerTab("log"); setShowViewer(true); }}
              className="flex flex-1 items-center justify-center gap-1 rounded border border-minimax-border px-2 py-1 text-[11px] text-minimax-muted hover:text-minimax-fg"
            >
              <CommitIcon size={10} />
              View Log
            </button>
          </div>
        </div>
      ) : null}
      <GitViewerModal
        open={showViewer}
        onClose={() => setShowViewer(false)}
        initialTab={viewerTab}
      />
    </div>
  );
}

interface FileBucketProps {
  label: string;
  testId: string;
  paths: string[];
  /** Displayed when ``paths`` is empty. ``null`` hides the section. */
  empty: string | null;
}

function FileBucket({ label, testId, paths, empty }: FileBucketProps): JSX.Element | null {
  // Hide the entire section when there are no paths *and* the
  // caller passed a ``null`` empty hint. This is how the parent
  // signals "don't show this bucket on a clean tree".
  if (paths.length === 0 && empty === null) return null;
  return (
    <div data-testid={testId} className="py-1">
      <div className="flex items-center gap-1 px-2 pb-1 text-[11px] uppercase tracking-wide text-minimax-muted">
        {label}
        {paths.length > 0 ? (
          <span data-testid={`${testId}-count`} className="ml-1 text-minimax-fg/80">
            ({paths.length})
          </span>
        ) : null}
      </div>
      {paths.length === 0 ? (
        <div className="px-2 py-1 text-minimax-muted">{empty}</div>
      ) : (
        <ul className="space-y-0.5">
          {paths.map((p) => (
            <li
              key={p}
              data-testid={`${testId}-item`}
              className="flex items-center gap-1.5 rounded px-2 py-1 font-mono text-[11px] text-minimax-fg hover:bg-minimax-border"
            >
              <FileText size={10} className="shrink-0 text-minimax-muted" />
              <span className="truncate">{p}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
