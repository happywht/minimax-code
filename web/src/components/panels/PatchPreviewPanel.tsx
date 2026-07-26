/**
 * PatchPreviewPanel — structured git diff preview with per-hunk
 * approve / reject actions.
 *
 * Layout: scope switcher (Working | Staged | Branch) + refresh, stats
 * strip, file-jump overview chips, then one PatchFileCard per changed
 * file. Hunk-level state (decisions, errors) lives here and is passed
 * down to the cards.
 */
import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { usePatchPreviewStore } from "../../stores";
import { IconButton } from "../../ui";
import type { PatchFile, PatchHunk } from "../../types/ipc";
import { PatchFileCard } from "./PatchFileCard";
import {
  SCOPES,
  fileKey,
  hunkKey,
  type DiffScope,
  type HunkDecision,
} from "./patchPreviewShared";

export interface PatchPreviewPanelProps {
  testId?: string;
}

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
        <div className="flex min-w-0 rounded-md border border-line bg-surface-2/40 p-0.5">
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
                  "flex h-6 items-center gap-1 rounded px-1.5 text-[11px] transition-colors duration-150 " +
                  (active
                    ? "bg-accent-subtle text-accent"
                    : "text-ink-1 hover:bg-surface-3 hover:text-ink-0")
                }
                title={`${label} diff`}
              >
                <Icon size={10} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
        <IconButton
          data-testid={`${testId}-refresh`}
          onClick={() => refreshAndReset()}
          disabled={loading}
          aria-label="Refresh patch preview"
        >
          <RefreshCw className={loading ? "animate-spin" : undefined} />
        </IconButton>
      </div>

      {error && (
        <div
          data-testid={`${testId}-error`}
          className="mt-2 rounded-md border border-status-error/30 bg-[var(--status-error-subtle)] px-2 py-1.5 text-[11px] text-status-error"
          title={error}
        >
          Unable to load diff
        </div>
      )}

      {!error && (
        <div
          data-testid={`${testId}-stats`}
          className="mt-2 flex items-center gap-3 rounded-md border border-line bg-surface-2/40 px-2 py-1.5 text-[11px] text-ink-1"
        >
          <span>{stats.files} file{stats.files === 1 ? "" : "s"}</span>
          <span className="text-status-success">+{stats.additions}</span>
          <span className="text-status-error">-{stats.deletions}</span>
        </div>
      )}

      {!loading && !error && files.length > 0 && (
        <div
          data-testid={`${testId}-file-overview`}
          className="mt-2 flex gap-1 overflow-x-auto rounded-md border border-line bg-surface-2/30 p-1"
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
                  "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 font-mono text-[11px] transition-colors duration-150 " +
                  (active
                    ? "bg-accent-subtle text-accent"
                    : "text-ink-1 hover:bg-surface-3 hover:text-ink-0")
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
          className="mt-2 rounded-md border border-line bg-surface-2/30 px-2 py-2 text-center text-[11px] italic text-ink-2"
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
