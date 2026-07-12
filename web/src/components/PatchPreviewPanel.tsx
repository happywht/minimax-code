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

type DiffScope = (typeof SCOPES)[number]["key"];
type HunkDecision = "approved" | "rejected" | "applying" | "rejecting" | "error";

export function PatchPreviewPanel({
  testId = "patch-preview-panel",
}: PatchPreviewPanelProps): JSX.Element {
  const scope = usePatchPreviewStore((s) => s.scope);
  const result = usePatchPreviewStore((s) => s.result);
  const loading = usePatchPreviewStore((s) => s.loading);
  const error = usePatchPreviewStore((s) => s.error);
  const setScope = usePatchPreviewStore((s) => s.setScope);
  const refresh = usePatchPreviewStore((s) => s.refresh);
  const applyHunk = usePatchPreviewStore((s) => s.applyHunk);
  const revertHunk = usePatchPreviewStore((s) => s.revertHunk);
  const fileRefs = useRef<Record<string, HTMLLIElement | null>>({});
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [hunkDecisions, setHunkDecisions] = useState<Record<string, HunkDecision>>({});
  const [hunkErrors, setHunkErrors] = useState<Record<string, string>>({});

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
    if (decision !== "error") {
      setHunkErrors((current) => {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  };

  const failHunk = (key: string, err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    setHunkDecisions((current) => ({ ...current, [key]: "error" }));
    setHunkErrors((current) => ({ ...current, [key]: message }));
  };

  const runHunkOperation = async (
    file: PatchFile,
    hunk: PatchHunk,
    hunkIndex: number,
    operation: "approve" | "reject",
  ) => {
    if (scope === "branch") return;
    if (operation === "approve" && scope !== "working") return;
    const key = hunkKey(file, hunk, hunkIndex);
    decideHunk(key, operation === "approve" ? "applying" : "rejecting");
    try {
      const payload = {
        scope,
        file_path: file.path,
        hunk_index: hunkIndex,
        old_start: hunk.old_start,
        new_start: hunk.new_start,
      };
      if (operation === "approve") {
        await applyHunk(payload);
        decideHunk(key, "approved");
      } else {
        await revertHunk(payload);
        decideHunk(key, "rejected");
      }
      await refresh();
    } catch (err) {
      failHunk(key, err);
    }
  };

  const refreshAndReset = (opts?: { scope?: DiffScope }) => {
    setHunkDecisions({});
    setHunkErrors({});
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
                  "flex h-6 items-center gap-1 rounded px-1.5 text-[11px] " +
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
                  "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 font-mono text-[11px] transition-colors duration-200 " +
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
              scope={scope}
              active={activeFile === fileKey(file)}
              decisions={hunkDecisions}
              errors={hunkErrors}
              onDecide={decideHunk}
              onApprove={(hunk, index) => void runHunkOperation(file, hunk, index, "approve")}
              onReject={(hunk, index) => void runHunkOperation(file, hunk, index, "reject")}
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
  scope,
  active,
  decisions,
  errors,
  onDecide,
  onApprove,
  onReject,
  itemRef,
}: {
  file: PatchFile;
  scope: DiffScope;
  active: boolean;
  decisions: Record<string, HunkDecision>;
  errors: Record<string, string>;
  onDecide: (key: string, decision: HunkDecision | null) => void;
  onApprove: (hunk: PatchHunk, index: number) => void;
  onReject: (hunk: PatchHunk, index: number) => void;
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
            <div className="truncate font-mono text-[11px] text-minimax-muted">
              {file.old_path} {"->"} {file.new_path}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={file.status} />
          <span className="font-mono text-[11px] text-status-success">+{file.additions}</span>
          <span className="font-mono text-[11px] text-status-error">-{file.deletions}</span>
        </div>
      </div>

      {file.binary ? (
        <div className="mt-1.5 rounded bg-minimax-panel px-2 py-1 text-[11px] text-minimax-muted">
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
                  scope={scope}
                  hunkIndex={index}
                  hunkKeyValue={key}
                  decision={decisions[key]}
                  error={errors[key]}
                  onDecide={onDecide}
                  onApprove={onApprove}
                  onReject={onReject}
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

function PatchLineContent({
  line,
  expanded,
}: {
  line: PatchLine;
  expanded: boolean;
}): JSX.Element {
  const prefix = line.kind === "add" ? "+" : line.kind === "delete" ? "-" : " ";
  const tone =
    line.kind === "add"
      ? "text-status-success"
      : line.kind === "delete"
        ? "text-status-error"
        : "text-minimax-muted";
  return (
    <span className={"block " + (expanded ? "whitespace-pre-wrap break-words " : "truncate ") + tone}>
      {prefix}{line.content}
    </span>
  );
}

function PatchHunkCard({
  hunk,
  scope,
  hunkIndex,
  hunkKeyValue,
  decision,
  error,
  onDecide,
  onApprove,
  onReject,
}: {
  hunk: PatchHunk;
  scope: DiffScope;
  hunkIndex: number;
  hunkKeyValue: string;
  decision?: HunkDecision;
  error?: string;
  onDecide: (key: string, decision: HunkDecision | null) => void;
  onApprove: (hunk: PatchHunk, index: number) => void;
  onReject: (hunk: PatchHunk, index: number) => void;
}): JSX.Element {
  const previewLines = useMemo(() => collectHunkPreviewLines(hunk), [hunk]);
  const [expanded, setExpanded] = useState(false);
  const busy = decision === "applying" || decision === "rejecting";
  const approveDisabled = busy || scope !== "working";
  const rejectDisabled = busy || scope === "branch";
  const visibleLines = expanded ? hunk.lines : previewLines;
  const canExpand =
    hunk.lines.length > previewLines.length ||
    hunk.lines.some((line) => line.kind === "context" || line.kind === "meta");
  return (
    <div
      data-testid={`patch-hunk-${hunkKeyValue}`}
      data-decision={decision ?? "pending"}
      className="overflow-hidden rounded border border-minimax-border bg-minimax-panel"
    >
      <div className="flex items-center justify-between gap-2 border-b border-minimax-border/60 px-2 py-1">
        <span className="min-w-0 truncate font-mono text-[11px] text-minimax-muted">
          @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <HunkDecisionBadge decision={decision} />
          <button
            type="button"
            data-testid={`patch-hunk-${hunkKeyValue}-approve`}
            onClick={() => onApprove(hunk, hunkIndex)}
            disabled={approveDisabled}
            className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-emerald-500/15 hover:text-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
            title={scope === "working" ? "Approve and stage this hunk" : "Only working hunks can be staged"}
            aria-label="Approve and stage this hunk"
          >
            {decision === "applying" ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} />}
          </button>
          <button
            type="button"
            data-testid={`patch-hunk-${hunkKeyValue}-reject`}
            onClick={() => onReject(hunk, hunkIndex)}
            disabled={rejectDisabled}
            className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-red-500/15 hover:text-status-error disabled:cursor-not-allowed disabled:opacity-40"
            title={scope === "staged" ? "Reject and unstage this hunk" : "Reject this hunk"}
            aria-label={scope === "staged" ? "Reject and unstage this hunk" : "Reject this hunk"}
          >
            {decision === "rejecting" ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
          </button>
          {decision && !busy && (
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
      {decision === "error" && error && (
        <div
          data-testid={`patch-hunk-${hunkKeyValue}-error`}
          className="border-b border-red-500/20 bg-red-500/10 px-2 py-1 text-[11px] text-status-error"
          title={error}
        >
          Operation failed
        </div>
      )}
      <pre
        className={
          "px-2 py-1 font-mono text-[11px] leading-relaxed " +
          (expanded ? "max-h-96 overflow-auto" : "max-h-28 overflow-hidden")
        }
      >
        {visibleLines.map((line, idx) => (
          <PatchLineContent key={idx} line={line} expanded={expanded} />
        ))}
      </pre>
      {canExpand && (
        <button
          type="button"
          data-testid={`patch-hunk-${hunkKeyValue}-toggle`}
          onClick={() => setExpanded((value) => !value)}
          className="border-t border-minimax-border/60 px-2 py-1 text-left text-[11px] text-minimax-muted transition-colors hover:bg-minimax-border/40 hover:text-minimax-fg"
        >
          {expanded ? "Show less" : "Show full hunk"}
        </button>
      )}
    </div>
  );
}

function HunkDecisionBadge({ decision }: { decision?: HunkDecision }): JSX.Element {
  if (decision === "applying") {
    return (
      <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1 py-0.5 text-[11px] text-emerald-300">
        staging
      </span>
    );
  }
  if (decision === "rejecting") {
    return (
      <span className="rounded border border-red-500/30 bg-red-500/10 px-1 py-0.5 text-[11px] text-status-error">
        rejecting
      </span>
    );
  }
  if (decision === "error") {
    return (
      <span className="rounded border border-red-500/30 bg-red-500/10 px-1 py-0.5 text-[11px] text-status-error">
        failed
      </span>
    );
  }
  if (decision === "approved") {
    return (
      <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1 py-0.5 text-[11px] text-emerald-300">
        approved
      </span>
    );
  }
  if (decision === "rejected") {
    return (
      <span className="rounded border border-red-500/30 bg-red-500/10 px-1 py-0.5 text-[11px] text-status-error">
        rejected
      </span>
    );
  }
  return (
    <span className="rounded border border-minimax-border bg-minimax-bg/40 px-1 py-0.5 text-[11px] text-minimax-muted">
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
  return <span className={"rounded border px-1 py-0.5 text-[11px] " + cls}>{status}</span>;
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
