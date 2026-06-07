/**
 * P2#26 regression test: ensure no font size below 11px in tsx/css files.
 *
 * The codebase convention is minimum 11px for readability.
 * This test scans all source files for text-[9px] or text-[10px]
 * (Tailwind arbitrary font sizes) and fails if found.
 */
import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";

function collectFiles(dir: string, ext: string[]): string[] {
  const result: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== "node_modules" && entry.name !== "dist") {
      result.push(...collectFiles(full, ext));
    } else if (entry.isFile() && ext.some((e) => entry.name.endsWith(e))) {
      result.push(full);
    }
  }
  return result;
}

describe("P2#26: minimum font size 11px", () => {
  it("no files should contain text-[9px] or text-[10px]", () => {
    const srcDir = path.resolve(__dirname, "..");
    const files = collectFiles(srcDir, [".tsx", ".css"]);

    const violations: string[] = [];
    const smallFontPattern = /text-\[(9|10)px\]/;

    for (const file of files) {
      const content = fs.readFileSync(file, "utf-8");
      const lines = content.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        // Skip comments
        if (line.trimStart().startsWith("//") || line.trimStart().startsWith("*")) continue;
        if (smallFontPattern.test(line)) {
          const rel = path.relative(srcDir, file);
          violations.push(`${rel}:${i + 1}: ${line.trim()}`);
        }
      }
    }

    expect(violations).toEqual([]);
    if (violations.length > 0) {
      console.error("Found sub-11px fonts:\n" + violations.join("\n"));
    }
  });
});
