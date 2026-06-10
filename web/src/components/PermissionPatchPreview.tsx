import { FileCode2 } from "lucide-react";
import type { PatchFile, PatchLine } from "../types/ipc";

export interface PermissionPatchPreviewProps {
  files: PatchFile[];
  testId?: string;
}

export function PermissionPatchPreview({
  files,
  testId = "permission-patch-preview",
}: PermissionPatchPreviewProps): JSX.Element | null {
  if (files.length === 0) return null;
  const stats = files.reduce(
    (acc, file) => ({
      files: acc.files + 1,
      additions: acc.additions + file.additions,
      deletions: acc.deletions + file.deletions,
    }),
    { files: 0, additions: 0, deletions: 0 },
  );

  return (
    <div data-testid={testId} className="mt-2 border-t border-minimax-border/70 pt-2">
      <div className="mb-1.5 flex items-center gap-2 text-[10px] text-minimax-muted">
        <FileCode2 size={11} />
        <span>{stats.files} file{stats.files === 1 ? "" : "s"}</span>
        <span className="text-status-success">+{stats.additions}</span>
        <span className="text-status-error">-{stats.deletions}</span>
      </div>
      <div className="space-y-2">
        {files.slice(0, 3).map((file) => (
          <PermissionPatchFile key={`${file.old_path}:${file.new_path}`} file={file} />
        ))}
      </div>
    </div>
  );
}

function PermissionPatchFile({ file }: { file: PatchFile }): JSX.Element {
  const lines = collectLines(file);
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-mono text-[10px] text-minimax-fg" title={file.path}>
          {file.path}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-minimax-muted">
          {file.status}
        </span>
      </div>
      {lines.length > 0 && (
        <pre className="mt-1 max-h-24 overflow-hidden rounded bg-minimax-bg/50 px-2 py-1 font-mono text-[10px] leading-relaxed">
          {lines.map((line, idx) => (
            <PermissionPatchLine key={idx} line={line} />
          ))}
        </pre>
      )}
    </div>
  );
}

function PermissionPatchLine({ line }: { line: PatchLine }): JSX.Element {
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

function collectLines(file: PatchFile): PatchLine[] {
  const lines: PatchLine[] = [];
  for (const hunk of file.hunks) {
    for (const line of hunk.lines) {
      if (line.kind === "add" || line.kind === "delete" || line.kind === "meta") {
        lines.push(line);
      }
      if (lines.length >= 10) return lines;
    }
  }
  return lines;
}
