/**
 * Markdown pre-processing helpers for chat messages.
 *
 * LLMs occasionally fuse the language identifier and the first code
 * line onto the opening fence (`` ```tsconst x = 1 ``). react-markdown
 * would treat the whole thing as the info string and lose the first
 * line, so we split it back onto its own line before rendering.
 */

/** Known fence languages, longest first so prefix matching is stable. */
const FENCE_LANGS = [
  "mermaid",
  "typescript",
  "javascript",
  "tsx",
  "jsx",
  "python",
  "json",
  "yaml",
  "bash",
  "shell",
  "html",
  "css",
  "sql",
  "toml",
  "rust",
  "go",
  "java",
  "cpp",
  "csharp",
  "markdown",
  "md",
  "py",
  "ts",
  "js",
  "sh",
].sort((a, b) => b.length - a.length);

/**
 * Repair fenced code blocks whose language tag is fused with the
 * first line of code. Returns the input untouched when no fences
 * are present.
 */
export function normalizeMarkdownForRender(text: string): string {
  if (!text.includes("```")) return text;
  return text
    .split("\n")
    .flatMap((line) => {
      const match = /^(\s*```)(\S+)(.*)$/.exec(line);
      if (!match) return [line];
      const [, fence, rawInfo, rest] = match;
      const lower = rawInfo.toLowerCase();
      const lang = FENCE_LANGS.find((candidate) => lower.startsWith(candidate));
      if (!lang || (rawInfo.length === lang.length && rest.length === 0)) return [line];
      if (rawInfo.length === lang.length && /^\s/.test(rest)) return [line];
      return [`${fence}${rawInfo.slice(0, lang.length)}`, `${rawInfo.slice(lang.length)}${rest}`];
    })
    .join("\n");
}
