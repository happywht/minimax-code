import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  Loader2,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react";
import { usePatchPreviewStore } from "../stores";
import type { PatchFile, PatchHunk, PatchLine } from "../types/ipc";

export interface PatchPreviewPanelProps {
  testId?: string;
}

const SCOPES = [
  { key: "working", label: "Working", icon: FileCode2 },
  { key: "staged", label: "Staged", icon: GitCommitHorizontal },
  { key: "branch", label: "Branch", icon: GitBranch },
] as const;

type HunkDecision = "approved" | "rejected";

export function PatchPreviewPanel({
  testId = "patch-preview-panel",
}: PatchPreviewPanelProps): JSX.Element {
  const scope = usePatchPreviewStore((s) => s.scope);
  const result = usePatchPreviewStore((s) => s.result);
  const loading = usePatchPreviewStore((s) => s.loading);
  const error = usePatchPreviewStore((s) => s.error);
  const setScope = usePatchPreviewStore((s) => s.setScope);
  const refresh = usePatchPreviewStore((s) => s.refresh);
  const fileRefs = useRef<Record<string, HTMLLIElement | null>>({});
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [hunkDecisions, setHunkDecisions] = useState<Record<string, HunkDecision>>({});

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const files = result?.files ?? [];
  const stats = result?.stats ?? { files: 0, additions: 0, deletions: 0 };

  const jumpToFile = (file: PatchFile) => {
    const key = fileKey(file);
    setActiveFile(key);
    fileRefs.current[key]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const decideHunk = (key: string, decision: HunkDecision | null) => {
    setHunkDecisions((current) => {
      const next = { ...current };
      if (decision === null) delete next[key];
      else next[key] = decision;
      return next;
    });
  };

  const refreshAndReset = (opts?: { scope?: typeof scope }) => {
    setHunkDecisions({});
    void refresh(opts);
  };

  return (
    <div data-testid={testId} className="px-3 pb-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 rounded border border-minimax-border bg-minimax-bg/40 p-0.5">
          {SCOPES.map(({ key, label, icon: Icon }) => {
            const active = scope === key;
            return (
              <button
                key={key}
                type="button"
                data-testid={`${testId}-scope-${key}`}
                onClick={() => {
                  setScope(key);
                  refreshAndReset({ scope: key });
                }}
                className={
                  "flex h-6 items-center gap-1 rounded px-1.5 text-[10px] " +
                  (active
                    ? "bg-minimax-accent/15 text-minimax-accent"
                    : "text-minimax-muted hover:bg-minimax-border/70 hover:text-minimax-fg")
                }
                title={`${label} diff`}
              >
                <Icon size={10} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
        <button
          type="button"
          data-testid={`${testId}-refresh`}
          onClick={() => refreshAndReset()}
          disabled={loading}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg disabled:opacity-50"
          aria-label="Refresh patch preview"
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        </button>
      </div>

      {error && (
        <div
          data-testid={`${testId}-error`}
          className="mt-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-status-error"
          title={error}
        >
          Unable to load diff
        </div>
      )}

      {!error && (
        <div
          data-testid={`${testId}-stats`}
          className="mt-2 flex items-center gap-3 rounded border border-minimax-border bg-minimax-bg/40 px-2 py-1.5 text-[11px] text-minimax-muted"
        >
          <span>{stats.files} file{stats.files === 1 ? "" : "s"}</span>
          <span className="text-status-success">+{stats.additions}</span>
          <span className="text-status-error">-{stats.deletions}</span>
        </div>
      )}

      {!loading && !error && files.length > 0 && (
        <div
          data-testid={`${testId}-file-overview`}
          className="mt-2 flex gap-1 overflow-x-auto rounded border border-minimax-border bg-minimax-bg/30 p-1"
          aria-label="Changed files"
        >
          {files.map((file) => {
            const key = fileKey(file);
            const active = activeFile === key;
            return (
              <button
                key={key}
                type="button"
                data-testid={`${testId}-file-jump-${file.path}`}
                onClick={() => jumpToFile(file)}
                className={
                  "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 font-mono text-[10px] transition-colors duration-200 " +
                  (active
                    ? "bg-minimax-accent/15 text-minimax-accent"
                    : "text-minimax-muted hover:bg-minimax-border/60 hover:text-minimax-fg")
                }
                title={`Jump to ${file.path}`}
              >
                <span className="max-w-32 truncate">{file.path}</span>
                <span className="text-status-success">+{file.additions}</span>
                <span className="text-status-error">-{file.deletions}</span>
              </button>
            );
          })}
        </div>
      )}

      {!loading && !error && files.length === 0 && (
        <div
          data-testid={`${testId}-empty`}
          className="mt-2 rounded border border-minimax-border bg-minimax-bg/30 px-2 py-2 text-center text-[11px] italic text-minimax-muted"
        >
          No changes in this scope.
        </div>
      )}

      {files.length > 0 && (
        <ul data-testid={`${testId}-files`} className="mt-2 space-y-1.5">
          {files.map((file) => (
            <PatchFileCard
              key={fileKey(file)}
              file={file}
              active={activeFile === fileKey(file)}
              decisions={hunkDecisions}
              onDecide={decideHunk}
              itemRef={(node) => {
                fileRefs.current[fileKey(file)] = node;
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function PatchFileCard({
  file,
  active,
  decisions,
  onDecide,
  itemRef,
}: {
  file: PatchFile;
  active: boolean;
  decisions: Record<string, HunkDecision>;
  onDecide: (key: string, decision: HunkDecision | null) => void;
  itemRef: (node: HTMLLIElement | null) => void;
}): JSX.Element {
  return (
    <li
      ref={itemRef}
      data-testid={`patch-file-card-${file.path}`}
      className={
        "rounded border bg-minimax-bg/40 p-2 transition-colors duration-200 " +
        (active ? "border-minimax-accent/60 ring-1 ring-minimax-accent/30" : "border-minimax-border")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[11px] text-minimax-fg" title={file.path}>
            {file.path}
          </div>
          {file.status === "renamed" && (
            <div className="truncate font-mono text-[10px] text-minimax-muted">
              {file.old_path} {"->"} {file.new_path}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={file.status} />
          <span className="font-mono text-[10px] text-status-success">+{file.additions}</span>
          <span className="font-mono text-[10px] text-status-error">-{file.deletions}</span>
        </div>
      </div>

      {file.binary ? (
        <div className="mt-1.5 rounded bg-minimax-panel px-2 py-1 text-[10px] text-minimax-muted">
          Binary file changed
        </div>
      ) : (
        file.hunks.length > 0 && (
          <div className="mt-1.5 space-y-1.5">
            {file.hunks.map((hunk, index) => {
              const key = hunkKey(file, hunk, index);
              return (
                <PatchHunkCard
                  key={key}
                  hunk={hunk}
                  hunkKeyValue={key}
                  decision={decisions[key]}
                  onDecide={onDecide}
                />
              );
            })}
          </div>
        )
      )}
    </li>
  );
}

function fileKey(file: PatchFile): string {
  return `${file.old_path}:${file.new_path}`;
}

function PatchLineRow({ line }: { line: PatchLine }): JSX.Element {
  const prefix = line.kind === "add" ? "+" : line.kind === "delete" ? "-" : " ";
  const tone =
    line.kind === "add"
      ? "text-status-success"
      : line.kind === "delete"
        ? "text-status-error"
        : "text-minimax-muted";
  return (
    <span className={"block truncate " + tone}>
      {prefix}{line.content}
    </span>
  );
}

function PatchHunkCard({
  hunk,
  hunkKeyValue,
  decision,
  onDecide,
}: {
  hunk: PatchHunk;
  hunkKeyValue: string;
  decision?: HunkDecision;
  onDecide: (key: string, decision: HunkDecision | null) => void;
}): JSX.Element {
  const previewLines = useMemo(() => collectHunkPreviewLines(hunk), [hunk]);
  return (
    <div
      data-testid={`patch-hunk-${hunkKeyValue}`}
      data-decision={decision ?? "pending"}
      className="overflow-hidden rounded border border-minimax-border bg-minimax-panel"
    >
      <div className="flex items-center justify-between gap-2 border-b border-minimax-border/60 px-2 py-1">
        <span className="min-w-0 truncate font-mono text-[10px] text-minimax-muted">
          @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <HunkDecisionBadge decision={decision} />
          <button
            type="button"
            data-testid={`patch-hunk-${hunkKeyValue}-approve`}
            onClick={() => onDecide(hunkKeyValue, "approved")}
            className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-emerald-500/15 hover:text-emerald-300"
            title="Approve this hunk"
            aria-label="Approve this hunk"
          >
            <Check size={10} />
          </button>
          <button
            type="button"
            data-testid={`patch-hunk-${hunkKeyValue}-reject`}
            onClick={() => onDecide(hunkKeyValue, "rejected")}
            className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-red-500/15 hover:text-status-error"
            title="Reject this hunk"
            aria-label="Reject this hunk"
          >
            <X size={10} />
          </button>
          {decision && (
            <button
              type="button"
              data-testid={`patch-hunk-${hunkKeyValue}-reset`}
              onClick={() => onDecide(hunkKeyValue, null)}
              className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border/70 hover:text-minimax-fg"
              title="Reset hunk decision"
              aria-label="Reset hunk decision"
            >
              <RotateCcw size={10} />
            </button>
          )}
        </div>
      </div>
      <pre className="max-h-28 overflow-hidden px-2 py-1 font-mono text-[10px] leading-relaxed">
        {previewLines.map((line, idx) => (
          <PatchLineRow key={idx} line={line} />
        ))}
      </pre>
    </div>
  );
}

function HunkDecisionBadge({ decision }: { decision?: HunkDecision }): JSX.Element {
  if (decision === "approved") {
    return (
      <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1 py-0.5 text-[10px] text-emerald-300">
        approved
      </span>
    );
  }
  if (decision === "rejected") {
    return (
      <span className="rounded border border-red-500/30 bg-red-500/10 px-1 py-0.5 text-[10px] text-status-error">
        rejected
      </span>
    );
  }
  return (
    <span className="rounded border border-minimax-border bg-minimax-bg/40 px-1 py-0.5 text-[10px] text-minimax-muted">
      pending
    </span>
  );
}

function StatusBadge({ status }: { status: PatchFile["status"] }): JSX.Element {
  const cls =
    status === "added"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
      : status === "deleted"
        ? "border-red-500/30 bg-red-500/10 text-status-error"
        : status === "renamed"
          ? "border-blue-500/30 bg-blue-500/10 text-blue-300"
          : "border-minimax-border bg-minimax-panel text-minimax-muted";
  return <span className={"rounded border px-1 py-0.5 text-[10px] " + cls}>{status}</span>;
}

function hunkKey(file: PatchFile, hunk: PatchHunk, index: number): string {
  return `${file.path}-${index}-${hunk.old_start}-${hunk.new_start}`;
}

function collectHunkPreviewLines(hunk: PatchHunk): PatchLine[] {
  const lines: PatchLine[] = [];
  for (const line of hunk.lines) {
    if (line.kind === "add" || line.kind === "delete") {
      lines.push(line);
    }
    if (lines.length >= 8) return lines;
  }
  return lines;
}
