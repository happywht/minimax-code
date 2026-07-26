/**
 * Shared constants, types, and helpers for the patch-preview components
 * (PatchPreviewPanel / PatchFileCard / PatchHunkCard).
 */
import { FileCode2, GitBranch, GitCommitHorizontal } from "lucide-react";
import type { PatchFile, PatchHunk, PatchLine } from "../../types/ipc";

export const SCOPES = [
  { key: "working", label: "Working", icon: FileCode2 },
  { key: "staged", label: "Staged", icon: GitCommitHorizontal },
  { key: "branch", label: "Branch", icon: GitBranch },
] as const;

export type DiffScope = (typeof SCOPES)[number]["key"];
export type HunkDecision = "approved" | "rejected" | "applying" | "rejecting" | "error";

export function fileKey(file: PatchFile): string {
  return `${file.old_path}:${file.new_path}`;
}

export function hunkKey(file: PatchFile, hunk: PatchHunk, index: number): string {
  return `${file.path}-${index}-${hunk.old_start}-${hunk.new_start}`;
}

/** First few add/delete lines — the collapsed preview of a hunk. */
export function collectHunkPreviewLines(hunk: PatchHunk): PatchLine[] {
  const lines: PatchLine[] = [];
  for (const line of hunk.lines) {
    if (line.kind === "add" || line.kind === "delete") {
      lines.push(line);
    }
    if (lines.length >= 8) return lines;
  }
  return lines;
}
