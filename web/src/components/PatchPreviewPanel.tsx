import { useEffect, useMemo } from "react";
import {
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { usePatchPreviewStore } from "../stores";
import type { PatchFile, PatchLine } from "../types/ipc";

export interface PatchPreviewPanelProps {
  testId?: string;
}

const SCOPES = [
  { key: "working", label: "Working", icon: FileCode2 },
  { key: "staged", label: "Staged", icon: GitCommitHorizontal },
  { key: "branch", label: "Branch", icon: GitBranch },
] as const;

export function PatchPreviewPanel({
  testId = "patch-preview-panel",
}: PatchPreviewPanelProps): JSX.Element {
  const scope = usePatchPreviewStore((s) => s.scope);
  const result = usePatchPreviewStore((s) => s.result);
  const loading = usePatchPreviewStore((s) => s.loading);
  const error = usePatchPreviewStore((s) => s.error);
  const setScope = usePatchPreviewStore((s) => s.setScope);
  const refresh = usePatchPreviewStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const files = result?.files ?? [];
  const stats = result?.stats ?? { files: 0, additions: 0, deletions: 0 };

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
                  void refresh({ scope: key });
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
          onClick={() => void refresh()}
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
            <PatchFileCard key={`${file.old_path}:${file.new_path}`} file={file} />
          ))}
        </ul>
      )}
    </div>
  );
}

function PatchFileCard({ file }: { file: PatchFile }): JSX.Element {
  const previewLines = useMemo(() => collectPreviewLines(file), [file]);
  return (
    <li className="rounded border border-minimax-border bg-minimax-bg/40 p-2">
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
        previewLines.length > 0 && (
          <pre className="mt-1.5 max-h-28 overflow-hidden rounded bg-minimax-panel px-2 py-1 font-mono text-[10px] leading-relaxed">
            {previewLines.map((line, idx) => (
              <PatchLineRow key={idx} line={line} />
            ))}
          </pre>
        )
      )}
    </li>
  );
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

function collectPreviewLines(file: PatchFile): PatchLine[] {
  const lines: PatchLine[] = [];
  for (const hunk of file.hunks) {
    for (const line of hunk.lines) {
      if (line.kind === "add" || line.kind === "delete") {
        lines.push(line);
      }
      if (lines.length >= 8) return lines;
    }
  }
  return lines;
}
