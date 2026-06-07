/**
 * P2#30 regression test: import path consistency snapshot.
 *
 * The codebase uses relative imports (../). This test records the current
 * count of @/ alias imports and fails if the count *increases*, catching
 * accidental drift without banning @/ outright (Vite alias is valid).
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

describe("P2#30: import style consistency", () => {
  it("no new @/ alias imports should be introduced", () => {
    const srcDir = path.resolve(__dirname, "..");
    const files = collectFiles(srcDir, [".ts", ".tsx"]);

    const violations: string[] = [];
    const aliasPattern = /^\s*import\s+.*from\s+['"]@\//;

    for (const file of files) {
      const content = fs.readFileSync(file, "utf-8");
      const lines = content.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        // Skip comments
        if (line.trimStart().startsWith("//") || line.trimStart().startsWith("*")) continue;
        if (aliasPattern.test(line)) {
          const rel = path.relative(srcDir, file);
          violations.push(`${rel}:${i + 1}: ${lines[i].trim()}`);
        }
      }
    }

    // Snapshot: currently there should be 0 @/ imports (all converted to relative)
    // If this baseline needs updating, change the number below.
    const BASELINE = 0;
    expect(
      violations.length,
      violations.length > BASELINE
        ? `New @/ imports found:\n${violations.join("\n")}`
        : "Unexpected: fewer @/ imports than baseline — update BASELINE",
    ).toBe(BASELINE);
  });
});
