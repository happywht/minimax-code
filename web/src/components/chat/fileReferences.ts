/**
 * File-reference extraction for chat message text.
 *
 * Scans assistant/system prose for path-like mentions
 * (``web/src/App.tsx:42``, ``agent/app.py#12``) and returns up to six
 * unique references. Fenced code blocks are stripped first so code
 * samples don't produce noise cards.
 */

export interface FileReference {
  path: string;
  line?: number;
}

const FILE_REF_EXTENSIONS = [
  "ts",
  "tsx",
  "js",
  "jsx",
  "py",
  "md",
  "json",
  "yaml",
  "yml",
  "toml",
  "css",
  "scss",
  "html",
  "sql",
  "rs",
  "go",
  "java",
  "cpp",
  "c",
  "cs",
  "sh",
  "ps1",
].sort((a, b) => b.length - a.length).join("|");

const FILE_REF_PATTERN = new RegExp(
  String.raw`(?:^|[\s(["'` + "`" + String.raw`])((?:[A-Za-z]:[\\/])?(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.(` +
    FILE_REF_EXTENSIONS +
    String.raw`))(?:[:#L](\d+))?`,
  "g",
);

function stripFencedCode(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "");
}

/** Extract unique file references (max 6) from message prose. */
export function extractFileReferences(text: string): FileReference[] {
  const withoutCode = stripFencedCode(text);
  const refs: FileReference[] = [];
  const seen = new Set<string>();
  for (const match of withoutCode.matchAll(FILE_REF_PATTERN)) {
    const path = match[1].replace(/\\/g, "/");
    const line = match[3] ? Number(match[3]) : undefined;
    const key = `${path}:${line ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push({ path, line });
    if (refs.length >= 6) break;
  }
  return refs;
}
