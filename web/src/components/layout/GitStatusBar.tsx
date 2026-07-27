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
import { useEffect, useRef, useState, useCallback } from "react";
import {
  GitBranch as Branch,
  CheckCircle2,
  CircleAlert,
  FileText,
  FileDiff,
  GitCommit as CommitIcon,
} from "lucide-react";
import { Button } from "../../ui/Button";
import { Spinner } from "../../ui/Spinner";
import { Badge } from "../../ui/Badge";
import { Panel } from "../../ui/Panel";
import { useGitStore } from "../../stores";
import { useClickOutside } from "../../lib/useClickOutside";
import { GitViewerModal } from "../modals/GitViewerModal";

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

  // Close on outside click — shared hook replaces inline mousedown listener.
  const closePopover = useCallback(() => setOpen(false), []);
  useClickOutside(containerRef, closePopover, { enabled: open });

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
    <div ref={containerRef} className="relative min-w-0" data-testid={testId}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        icon={<Branch size={12} className="text-ink-2" />}
        onClick={onOpen}
        data-testid="git-status-bar-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={
          status
            ? `Branch ${branch} — ${indicatorLabel}`
            : "Loading git status…"
        }
        className="min-w-0 max-w-[132px] justify-start sm:max-w-[220px]"
      >
        <span
          data-testid="git-status-bar-branch"
          className="min-w-0 truncate font-mono"
        >
          {branch}
        </span>
        <span aria-hidden="true" className="text-ink-2">
          ·
        </span>
        <span
          data-testid="git-status-bar-indicator"
          className={
            "flex items-center gap-1 " +
            (clean === null
              ? "text-ink-1"
              : clean
                ? "text-status-success"
                : "text-status-warning")
          }
        >
          {clean === null ? (
            <span data-testid="git-status-bar-loading">
              <Spinner size={10} />
            </span>
          ) : clean ? (
            <CheckCircle2 size={10} data-testid="git-status-bar-icon-clean" />
          ) : (
            <CircleAlert size={10} data-testid="git-status-bar-icon-dirty" />
          )}
          <span className="hidden sm:inline">{indicatorLabel}</span>
          {loading && clean !== null ? (
            <Spinner
              size={10}
              className="text-ink-2"
              aria-hidden="true"
            />
          ) : null}
        </span>
      </Button>
      {open ? (
        <Panel
          data-testid="git-status-bar-popover"
          role="dialog"
          aria-label={`Git status on ${branch}`}
          title={
            <span className="normal-case">
              <span className="flex items-center gap-2 text-ink-0">
                <Branch size={12} className="text-ink-2" />
                <span className="font-mono">{branch}</span>
              </span>
            </span>
          }
          actions={
            <>
              {status && status.ahead > 0 ? (
                <Badge
                  tone="success"
                  data-testid="git-status-bar-ahead"
                  className="ml-auto"
                >
                  ↑ {status.ahead}
                </Badge>
              ) : null}
              {status && status.behind > 0 ? (
                <Badge
                  tone="warning"
                  data-testid="git-status-bar-behind"
                >
                  ↓ {status.behind}
                </Badge>
              ) : null}
            </>
          }
          className="absolute left-0 top-full z-50 mt-1 w-72 shadow-pop"
          flush
        >
          <div className="max-h-72 overflow-y-auto px-1 py-1 text-xs">
            <FileBucket
              label="Modified"
              testId="git-status-bar-modified"
              paths={status?.modified ?? []}
              empty={clean === true ? "暂无修改文件" : null}
            />
            <FileBucket
              label="Staged"
              testId="git-status-bar-staged"
              paths={status?.staged ?? []}
              empty={clean === true ? "暂无暂存文件" : null}
            />
            <FileBucket
              label="Untracked"
              testId="git-status-bar-untracked"
              paths={status?.untracked ?? []}
              empty={clean === true ? "暂无未跟踪文件" : null}
            />
            {status === null ? (
              <div
                data-testid="git-status-bar-empty"
                className="px-3 py-2 text-ink-1"
              >
                正在加载 Git 状态…
              </div>
            ) : clean ? (
              <div
                data-testid="git-status-bar-clean-message"
                className="px-3 py-2 text-ink-1"
              >
                Working tree clean.
              </div>
            ) : null}
          </div>
          <div className="flex gap-2 border-t border-line px-2 py-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              data-testid="git-status-bar-view-diff"
              onClick={() => {
                setViewerTab("diff");
                setShowViewer(true);
              }}
              className="flex-1"
            >
              <FileDiff size={12} />
              View Diff
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              data-testid="git-status-bar-view-log"
              onClick={() => {
                setViewerTab("log");
                setShowViewer(true);
              }}
              className="flex-1"
            >
              <CommitIcon size={12} />
              View Log
            </Button>
          </div>
        </Panel>
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
      <div className="flex items-center gap-1 px-2 pb-1 text-[11px] uppercase tracking-wide text-ink-1">
        {label}
        {paths.length > 0 ? (
          <span data-testid={`${testId}-count`} className="ml-1 text-ink-1">
            ({paths.length})
          </span>
        ) : null}
      </div>
      {paths.length === 0 ? (
        <div className="px-2 py-1 text-ink-1">{empty}</div>
      ) : (
        <ul className="space-y-0.5">
          {paths.map((p) => (
            <li
              key={p}
              data-testid={`${testId}-item`}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-[11px] text-ink-0 transition-colors hover:bg-surface-3"
            >
              <FileText size={10} className="shrink-0 text-ink-2" />
              <span className="truncate">{p}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
