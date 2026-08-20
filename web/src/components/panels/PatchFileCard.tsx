/**
 * PatchFileCard — one changed file inside the patch preview: header
 * (path, status badge, +/- counts) plus its hunk cards.
 */
import { useMemo, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Badge, IconButton, type BadgeTone } from "../../ui";
import { strings } from "../../ui/strings";
import type { PatchFile, PatchHunk } from "../../types/ipc";
import { PatchHunkCard } from "./PatchHunkCard";
import { hunkKey, type DiffScope, type HunkDecision } from "./patchPreviewShared";

const STATUS_TONE: Record<PatchFile["status"], BadgeTone> = {
  added: "success",
  deleted: "error",
  renamed: "info",
  modified: "neutral",
};

function StatusBadge({ status }: { status: PatchFile["status"] }): JSX.Element {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status}</Badge>;
}

export interface PatchFileCardProps {
  file: PatchFile;
  scope: DiffScope;
  active: boolean;
  decisions: Record<string, HunkDecision>;
  errors: Record<string, string>;
  onDecide: (key: string, decision: HunkDecision | null) => void;
  onApprove: (hunk: PatchHunk, index: number) => void;
  onReject: (hunk: PatchHunk, index: number) => void;
  onApproveFile: (file: PatchFile) => void;
  onRejectFile: (file: PatchFile) => void;
  itemRef: (node: HTMLLIElement | null) => void;
}

export function PatchFileCard({
  file,
  scope,
  active,
  decisions,
  errors,
  onDecide,
  onApprove,
  onReject,
  onApproveFile,
  onRejectFile,
  itemRef,
}: PatchFileCardProps): JSX.Element {
  const [fileBusy, setFileBusy] = useState(false);
  const fileDecision = useMemo(() => {
    if (file.binary || file.hunks.length === 0) return null;
    const hunkStates = file.hunks.map((h, i) => decisions[hunkKey(file, h, i)]);
    if (hunkStates.every((d) => d === "approved")) return "approved";
    if (hunkStates.every((d) => d === "rejected")) return "rejected";
    if (hunkStates.some((d) => d === "applying" || d === "rejecting")) return "busy";
    if (hunkStates.some((d) => d === "error")) return "error";
    return null;
  }, [file, decisions]);

  const runFileOperation = async (operation: "approve" | "reject") => {
    if (scope === "branch" || file.binary) return;
    setFileBusy(true);
    try {
      if (operation === "approve") {
        await onApproveFile(file);
      } else {
        await onRejectFile(file);
      }
    } finally {
      setFileBusy(false);
    }
  };

  return (
    <li
      ref={itemRef}
      data-testid={`patch-file-card-${file.path}`}
      className={
        "rounded-md border bg-surface-2/40 p-2 transition-colors duration-150 " +
        (active ? "border-accent/60 ring-1 ring-accent/30" : "border-line")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[11px] text-ink-0" title={file.path}>
            {file.path}
          </div>
          {file.status === "renamed" && (
            <div className="truncate font-mono text-[11px] text-ink-2">
              {file.old_path} {"->"} {file.new_path}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {fileDecision && fileDecision !== "busy" && (
            <Badge tone={fileDecision === "approved" ? "success" : fileDecision === "rejected" ? "error" : "neutral"}>
              {fileDecision === "approved"
                ? strings.panels.patch.decisionApproved
                : fileDecision === "rejected"
                  ? strings.panels.patch.decisionRejected
                  : strings.panels.patch.decisionFailed}
            </Badge>
          )}
          <StatusBadge status={file.status} />
          <span className="font-mono text-[11px] text-status-success">+{file.additions}</span>
          <span className="font-mono text-[11px] text-status-error">-{file.deletions}</span>
          {!file.binary && scope !== "branch" && (
            <>
              <IconButton
                size="sm"
                data-testid={`patch-file-card-${file.path}-approve`}
                onClick={() => void runFileOperation("approve")}
                disabled={fileBusy || scope !== "working"}
                className="h-5 w-5 hover:bg-[var(--status-success-subtle)] hover:text-status-success"
                title={scope === "working" ? strings.panels.patch.fileApproveTitle : strings.panels.patch.fileApproveOnlyWorking}
                aria-label={strings.panels.patch.fileApproveTitle}
              >
                {fileBusy ? <Loader2 className="animate-spin" /> : <Check />}
              </IconButton>
              <IconButton
                size="sm"
                data-testid={`patch-file-card-${file.path}-reject`}
                onClick={() => void runFileOperation("reject")}
                disabled={fileBusy}
                className="h-5 w-5 hover:bg-[var(--status-error-subtle)] hover:text-status-error"
                title={scope === "staged" ? strings.panels.patch.fileUnstageTitle : strings.panels.patch.fileRejectTitle}
                aria-label={scope === "staged" ? strings.panels.patch.fileUnstageTitle : strings.panels.patch.fileRejectTitle}
              >
                {fileBusy ? <Loader2 className="animate-spin" /> : <X />}
              </IconButton>
            </>
          )}
        </div>
      </div>

      {file.binary ? (
        <div className="mt-1.5 rounded-md bg-surface-1 px-2 py-1 text-[11px] text-ink-2">
          {strings.panels.patch.binaryChanged}
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
