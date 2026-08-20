/**
 * Static a11y audit — every icon-only `<button>` in production sources
 * must carry an accessible name (aria-label / aria-labelledby).
 *
 * Rationale: an icon-only button has no text content, so screen readers
 * announce nothing without an explicit label. The audit scans compiled
 * JSX statically, so a label-less icon button fails CI instead of
 * shipping silently (R36, M6 accessibility milestone).
 *
 * Method:
 *   - collect production .tsx files under src/ (skip __tests__ dirs and
 *     colocated *.test.tsx)
 *   - match each <button ...>...</button> block
 *   - "icon-only" = no visible text after stripping tags/JSX comments,
 *     and the first child is a capitalized component (lucide icon etc.)
 *   - sr-only text counts as a label (it survives the text strip)
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// vitest runs with cwd = web/ (jsdom rewrites import.meta.url to http,
// so process.cwd() is the portable anchor here; the file-count assertion
// below guards against a wrong cwd failing silently)
const SRC_ROOT = join(process.cwd(), "src");

function collectTsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "__tests__" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectTsxFiles(full));
    } else if (entry.endsWith(".tsx") && !entry.includes(".test.")) {
      out.push(full);
    }
  }
  return out;
}

// `(?:(?!>)[\s\S])*` = dotall tempered token: crosses newlines inside the
// opening tag (multi-line attribute lists) without running past its `>`.
const BUTTON_RE = /<button(?:(?!>)[\s\S])*>([\s\S]*?)<\/button>/g;

/** Strip JSX comments and tags; whatever remains is the text content. */
function visibleText(body: string): string {
  return body
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/<[\s\S]*?>/g, "")
    .trim();
}

function isIconOnly(body: string): boolean {
  if (visibleText(body) !== "") return false;
  return /^\s*<[A-Z][\w.]*[\s/>]/.test(body);
}

describe("a11y audit: icon-only buttons", () => {
  const files = collectTsxFiles(SRC_ROOT);

  it("scans a non-trivial number of production files", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("every icon-only button has an accessible name", () => {
    const violations: string[] = [];
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      for (const m of src.matchAll(BUTTON_RE)) {
        const [block, body] = m;
        if (!isIconOnly(body)) continue;
        if (!/aria-label(?:ledby)?\s*=/.test(block)) {
          const line = src.slice(0, m.index ?? 0).split("\n").length;
          violations.push(`${file.split(/[\\/]/).slice(-3).join("/")}:${line}`);
        }
      }
    }
    expect(violations, `icon-only buttons without aria-label:\n${violations.join("\n")}`).toEqual([]);
  });
});
