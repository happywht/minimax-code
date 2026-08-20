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
import { Archive, CheckCheck, RefreshCw, X } from "lucide-react";
import { toast } from "../layout/ErrorBoundary";
import { usePatchPreviewStore } from "../../stores";
import { Button, IconButton } from "../../ui";
import { strings } from "../../ui/strings";
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
  const globalBusy = usePatchPreviewStore((s) => s.globalBusy);
  const setScope = usePatchPreviewStore((s) => s.setScope);
  const refresh = usePatchPreviewStore((s) => s.refresh);
  const applyHunk = usePatchPreviewStore((s) => s.applyHunk);
  const revertHunk = usePatchPreviewStore((s) => s.revertHunk);
  const applyFile = usePatchPreviewStore((s) => s.applyFile);
  const revertFile = usePatchPreviewStore((s) => s.revertFile);
  const applyAll = usePatchPreviewStore((s) => s.applyAll);
  const revertAll = usePatchPreviewStore((s) => s.revertAll);
  const saveSnapshot = usePatchPreviewStore((s) => s.saveSnapshot);
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

  const runFileOperation = async (file: PatchFile, operation: "approve" | "reject") => {
    if (scope === "branch" || file.binary) return;
    const keys = file.hunks.map((h, i) => hunkKey(file, h, i));
    keys.forEach((key) => decideHunk(key, operation === "approve" ? "applying" : "rejecting"));
    try {
      if (operation === "approve") {
        await applyFile({ scope, file_path: file.path });
        keys.forEach((key) => decideHunk(key, "approved"));
      } else {
        await revertFile({ scope, file_path: file.path });
        keys.forEach((key) => decideHunk(key, "rejected"));
      }
      await refresh();
    } catch (err) {
      keys.forEach((key) => failHunk(key, err));
    }
  };

  const runGlobalOperation = async (operation: "apply_all" | "revert_all" | "save_snapshot") => {
    if (scope === "branch" && operation !== "save_snapshot") return;
    try {
      if (operation === "save_snapshot") {
        const res = await saveSnapshot();
        if (res.clean) {
          toast.success(strings.panels.patch.snapshotClean);
        } else {
          toast.success(strings.panels.patch.snapshotSaved, res.snapshot_ref ?? undefined);
        }
        return;
      }
      const res = operation === "apply_all" ? await applyAll({ scope }) : await revertAll({ scope });
      if (!res.ok || res.failed.length > 0) {
        const detail = res.failed.map((f) => `${f.file_path}: ${f.error}`).join("; ");
        toast.error(strings.panels.patch.partialFail, detail || strings.panels.patch.partialFailDetail);
      } else {
        toast.success(
          operation === "apply_all"
            ? strings.panels.patch.allApplied
            : strings.panels.patch.allReverted,
        );
      }
      refreshAndReset();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.panels.patch.opFail, message);
    }
  };

  const canApplyAll = scope === "working" && files.length > 0 && !globalBusy;
  const canRevertAll = scope !== "branch" && files.length > 0 && !globalBusy;

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
                title={strings.panels.patch.scopeTitle(label)}
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
          aria-label={strings.panels.patch.refreshAria}
        >
          <RefreshCw className={loading ? "animate-spin" : undefined} />
        </IconButton>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="subtle"
          data-testid={`${testId}-snapshot`}
          onClick={() => void runGlobalOperation("save_snapshot")}
          disabled={globalBusy}
          className="gap-1"
        >
          <Archive size={12} />
          {strings.panels.patch.snapshotLabel}
        </Button>
        <Button
          size="sm"
          variant="subtle"
          data-testid={`${testId}-apply-all`}
          onClick={() => void runGlobalOperation("apply_all")}
          disabled={!canApplyAll}
          className="gap-1"
        >
          <CheckCheck size={12} />
          {strings.panels.patch.applyAll}
        </Button>
        <Button
          size="sm"
          variant="subtle"
          data-testid={`${testId}-revert-all`}
          onClick={() => void runGlobalOperation("revert_all")}
          disabled={!canRevertAll}
          className="gap-1"
        >
          <X size={12} />
          {strings.panels.patch.revertAll}
        </Button>
      </div>

      {error && (
        <div
          data-testid={`${testId}-error`}
          className="mt-2 rounded-md border border-status-error/30 bg-[var(--status-error-subtle)] px-2 py-1.5 text-[11px] text-status-error"
          title={error}
        >
          {strings.panels.patch.loadFail}
        </div>
      )}

      {!error && (
        <div
          data-testid={`${testId}-stats`}
          className="mt-2 flex items-center gap-3 rounded-md border border-line bg-surface-2/40 px-2 py-1.5 text-[11px] text-ink-1"
        >
          <span>{strings.panels.fileCount(stats.files)}</span>
          <span className="text-status-success">+{stats.additions}</span>
          <span className="text-status-error">-{stats.deletions}</span>
        </div>
      )}

      {!loading && !error && files.length > 0 && (
        <div
          data-testid={`${testId}-file-overview`}
          className="mt-2 flex gap-1 overflow-x-auto rounded-md border border-line bg-surface-2/30 p-1"
          aria-label={strings.panels.patch.changedFilesAria}
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
                title={strings.panels.patch.jumpTitle(file.path)}
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
          当前范围没有变更。
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
              onApproveFile={(f) => void runFileOperation(f, "approve")}
              onRejectFile={(f) => void runFileOperation(f, "reject")}
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
