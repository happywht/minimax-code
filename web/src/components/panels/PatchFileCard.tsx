/**
 * PatchFileCard — one changed file inside the patch preview: header
 * (path, status badge, +/- counts) plus its hunk cards.
 */
import { Badge, type BadgeTone } from "../../ui";
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
  itemRef,
}: PatchFileCardProps): JSX.Element {
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
          <StatusBadge status={file.status} />
          <span className="font-mono text-[11px] text-status-success">+{file.additions}</span>
          <span className="font-mono text-[11px] text-status-error">-{file.deletions}</span>
        </div>
      </div>

      {file.binary ? (
        <div className="mt-1.5 rounded-md bg-surface-1 px-2 py-1 text-[11px] text-ink-2">
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
