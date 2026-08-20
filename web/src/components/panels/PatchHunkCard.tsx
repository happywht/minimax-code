/**
 * PatchHunkCard — a single diff hunk with approve / reject / reset
 * actions and an expandable line preview.
 */
import { useMemo, useState } from "react";
import { Check, Loader2, RotateCcw, X } from "lucide-react";
import { Badge, IconButton, type BadgeTone } from "../../ui";
import { strings } from "../../ui/strings";
import type { PatchHunk, PatchLine } from "../../types/ipc";
import {
  collectHunkPreviewLines,
  type DiffScope,
  type HunkDecision,
} from "./patchPreviewShared";

const DECISION_DISPLAY: Record<HunkDecision, { tone: BadgeTone; label: string }> = {
  applying: { tone: "success", label: strings.panels.patch.decisionStaging },
  approved: { tone: "success", label: strings.panels.patch.decisionApproved },
  rejecting: { tone: "error", label: strings.panels.patch.decisionRejecting },
  rejected: { tone: "error", label: strings.panels.patch.decisionRejected },
  error: { tone: "error", label: strings.panels.patch.decisionFailed },
};

export function PatchLineContent({
  line,
  expanded,
}: {
  line: PatchLine;
  expanded: boolean;
}): JSX.Element {
  const prefix = line.kind === "add" ? "+" : line.kind === "delete" ? "-" : " ";
  const tone =
    line.kind === "add"
      ? "bg-[var(--status-success-subtle)] text-status-success"
      : line.kind === "delete"
        ? "bg-[var(--status-error-subtle)] text-status-error"
        : "text-ink-2";
  return (
    <span
      className={
        "block px-1 " + (expanded ? "whitespace-pre-wrap break-words " : "truncate ") + tone
      }
    >
      {prefix}{line.content}
    </span>
  );
}

function HunkDecisionBadge({ decision }: { decision?: HunkDecision }): JSX.Element {
  const display = decision
    ? DECISION_DISPLAY[decision]
    : { tone: "neutral" as const, label: strings.panels.patch.decisionPending };
  return <Badge tone={display.tone}>{display.label}</Badge>;
}

export interface PatchHunkCardProps {
  hunk: PatchHunk;
  scope: DiffScope;
  hunkIndex: number;
  hunkKeyValue: string;
  decision?: HunkDecision;
  error?: string;
  onDecide: (key: string, decision: HunkDecision | null) => void;
  onApprove: (hunk: PatchHunk, index: number) => void;
  onReject: (hunk: PatchHunk, index: number) => void;
}

export function PatchHunkCard({
  hunk,
  scope,
  hunkIndex,
  hunkKeyValue,
  decision,
  error,
  onDecide,
  onApprove,
  onReject,
}: PatchHunkCardProps): JSX.Element {
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
      className="overflow-hidden rounded-md border border-line bg-surface-1"
    >
      <div className="flex items-center justify-between gap-2 border-b border-line/60 px-2 py-1">
        <span className="min-w-0 truncate font-mono text-[11px] text-ink-2">
          @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <HunkDecisionBadge decision={decision} />
          <IconButton
            size="sm"
            data-testid={`patch-hunk-${hunkKeyValue}-approve`}
            onClick={() => onApprove(hunk, hunkIndex)}
            disabled={approveDisabled}
            className="h-5 w-5 hover:bg-[var(--status-success-subtle)] hover:text-status-success"
            title={scope === "working" ? strings.panels.patch.hunkApproveTitle : strings.panels.patch.hunkOnlyWorkingStaged}
            aria-label={strings.panels.patch.hunkApproveTitle}
          >
            {decision === "applying" ? <Loader2 className="animate-spin" /> : <Check />}
          </IconButton>
          <IconButton
            size="sm"
            data-testid={`patch-hunk-${hunkKeyValue}-reject`}
            onClick={() => onReject(hunk, hunkIndex)}
            disabled={rejectDisabled}
            className="h-5 w-5 hover:bg-[var(--status-error-subtle)] hover:text-status-error"
            title={scope === "staged" ? strings.panels.patch.hunkUnstageTitle : strings.panels.patch.hunkRejectTitle}
            aria-label={scope === "staged" ? strings.panels.patch.hunkUnstageTitle : strings.panels.patch.hunkRejectTitle}
          >
            {decision === "rejecting" ? <Loader2 className="animate-spin" /> : <X />}
          </IconButton>
          {decision && !busy && (
            <IconButton
              size="sm"
              data-testid={`patch-hunk-${hunkKeyValue}-reset`}
              onClick={() => onDecide(hunkKeyValue, null)}
              className="h-5 w-5"
              title={strings.panels.patch.hunkResetTitle}
              aria-label={strings.panels.patch.hunkResetTitle}
            >
              <RotateCcw />
            </IconButton>
          )}
        </div>
      </div>
      {decision === "error" && error && (
        <div
          data-testid={`patch-hunk-${hunkKeyValue}-error`}
          className="border-b border-status-error/20 bg-[var(--status-error-subtle)] px-2 py-1 text-[11px] text-status-error"
          title={error}
        >
          {strings.panels.patch.hunkOpFailed}
        </div>
      )}
      <pre
        className={
          "py-1 font-mono text-[11px] leading-relaxed " +
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
          className="w-full border-t border-line/60 px-2 py-1 text-left text-[11px] text-ink-2 transition-colors duration-150 hover:bg-surface-3 hover:text-ink-0"
        >
          {expanded ? strings.panels.patch.showLess : strings.panels.patch.showFullHunk}
        </button>
      )}
    </div>
  );
}
