import { useMemo, useState } from "react";
import { FileCode2 } from "lucide-react";
import { Button } from "../../ui";
import { strings } from "../../ui/strings";
import type { PatchFile, PatchLine } from "../../types/ipc";

export interface PermissionPatchPreviewProps {
  files: PatchFile[];
  testId?: string;
}

export function PermissionPatchPreview({
  files,
  testId = "permission-patch-preview",
}: PermissionPatchPreviewProps): JSX.Element | null {
  const [expanded, setExpanded] = useState(false);
  const fileSummaries = useMemo(
    () => files.map((file) => ({ file, lines: collectLines(file) })),
    [files],
  );
  const stats = files.reduce(
    (acc, file) => ({
      files: acc.files + 1,
      additions: acc.additions + file.additions,
      deletions: acc.deletions + file.deletions,
    }),
    { files: 0, additions: 0, deletions: 0 },
  );
  if (files.length === 0) return null;
  const needsExpand =
    files.length > 3 || fileSummaries.some(({ lines }) => lines.length > 10);
  const visibleFiles = expanded ? fileSummaries : fileSummaries.slice(0, 3);

  return (
    <div data-testid={testId} className="mt-2 border-t border-line/70 pt-2">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] text-ink-2">
        <FileCode2 size={11} />
        <span>{strings.modals.fileCount(stats.files)}</span>
        <span className="text-status-success">+{stats.additions}</span>
        <span className="text-status-error">-{stats.deletions}</span>
      </div>
      <div className="space-y-2">
        {visibleFiles.map(({ file, lines }) => (
          <PermissionPatchFile
            key={`${file.old_path}:${file.new_path}`}
            file={file}
            lines={expanded ? lines : lines.slice(0, 10)}
            expanded={expanded}
          />
        ))}
      </div>
      {needsExpand && (
        <Button
          variant="secondary"
          size="sm"
          data-testid={`${testId}-toggle`}
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 h-6 px-2 text-[11px]"
        >
          {expanded ? strings.modals.patchPreview.showLess : strings.modals.patchPreview.showFullPatch}
        </Button>
      )}
    </div>
  );
}

function PermissionPatchFile({
  file,
  lines,
  expanded,
}: {
  file: PatchFile;
  lines: PatchLine[];
  expanded: boolean;
}): JSX.Element {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-mono text-[11px] text-ink-0" title={file.path}>
          {file.path}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-ink-2">
          {file.status}
        </span>
      </div>
      {lines.length > 0 && (
        <pre
          className={
            "mt-1 rounded-md bg-surface-0/60 py-1 font-mono text-[11px] leading-relaxed " +
            (expanded ? "max-h-72 overflow-auto" : "max-h-24 overflow-hidden")
          }
        >
          {lines.map((line, idx) => (
            <PermissionPatchLine key={idx} line={line} expanded={expanded} />
          ))}
        </pre>
      )}
    </div>
  );
}

function PermissionPatchLine({
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
    <span className={"block px-2 " + (expanded ? "whitespace-pre-wrap break-words " : "truncate ") + tone}>
      {prefix}{line.content}
    </span>
  );
}

function collectLines(file: PatchFile): PatchLine[] {
  const lines: PatchLine[] = [];
  for (const hunk of file.hunks) {
    for (const line of hunk.lines) {
      if (line.kind === "add" || line.kind === "delete" || line.kind === "meta") {
        lines.push(line);
      }
    }
  }
  return lines;
}
