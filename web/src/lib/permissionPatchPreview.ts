import type { PatchFile, PatchLine } from "../types/ipc";

const MAX_RENDERED_LINES = 120;

export function buildPermissionPatchFiles(
  tool: string,
  args: Record<string, unknown>,
): PatchFile[] {
  if (tool === "edit_file") return buildEditPreview(args);
  if (tool === "write_file") return buildWritePreview(args);
  return [];
}

function buildEditPreview(args: Record<string, unknown>): PatchFile[] {
  const path = stringArg(args.path);
  const oldText = stringArg(args.old_string);
  const newText = stringArg(args.new_string);
  if (!path || oldText == null || newText == null) return [];

  const oldLines = splitLines(oldText);
  const newLines = splitLines(newText);
  const lines: PatchLine[] = [
    ...oldLines.slice(0, MAX_RENDERED_LINES).map((content, i) => ({
      kind: "delete" as const,
      old_line: i + 1,
      new_line: null,
      content,
    })),
    ...newLines.slice(0, MAX_RENDERED_LINES).map((content, i) => ({
      kind: "add" as const,
      old_line: null,
      new_line: i + 1,
      content,
    })),
  ];
  appendTruncation(lines, oldLines.length + newLines.length);
  return [makeFile(path, "modified", oldLines.length, newLines.length, lines)];
}

function buildWritePreview(args: Record<string, unknown>): PatchFile[] {
  const path = stringArg(args.path);
  const content = stringArg(args.content);
  if (!path || content == null) return [];

  const newLines = splitLines(content);
  const lines: PatchLine[] = newLines.slice(0, MAX_RENDERED_LINES).map((line, i) => ({
    kind: "add",
    old_line: null,
    new_line: i + 1,
    content: line,
  }));
  appendTruncation(lines, newLines.length);
  return [makeFile(path, "added", 0, newLines.length, lines)];
}

function makeFile(
  path: string,
  status: PatchFile["status"],
  deletions: number,
  additions: number,
  lines: PatchLine[],
): PatchFile {
  return {
    path,
    old_path: path,
    new_path: path,
    status,
    additions,
    deletions,
    binary: false,
    hunks: [
      {
        old_start: 1,
        old_lines: deletions,
        new_start: 1,
        new_lines: additions,
        header: "",
        lines,
      },
    ],
  };
}

function appendTruncation(lines: PatchLine[], totalLines: number): void {
  if (totalLines <= MAX_RENDERED_LINES) return;
  lines.push({
    kind: "meta",
    old_line: null,
    new_line: null,
    content: `... ${totalLines - MAX_RENDERED_LINES} more line(s) hidden`,
  });
}

function stringArg(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function splitLines(value: string): string[] {
  if (value.length === 0) return [];
  const lines = value.replace(/\r\n/g, "\n").split("\n");
  if (lines[lines.length - 1] === "") return lines.slice(0, -1);
  return lines;
}
