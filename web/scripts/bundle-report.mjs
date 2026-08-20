#!/usr/bin/env node
/**
 * First-screen bundle audit for the production build.
 *
 * Measures what the browser must download *synchronously* to paint the
 * app shell: the <script>/<link> assets dist/index.html references
 * directly. Everything else (MessageItem's markdown+katex pipeline,
 * shiki language packs, mermaid diagram packs, Settings/PreviewPanel/
 * SkillsPanel) is behind dynamic imports and deliberately excluded.
 *
 * Fails (exit 1) when the first-screen payload exceeds the budget, so
 * a regression (e.g. a new heavy static import into App.tsx) is caught
 * at build-review time instead of in the field.
 *
 * Usage:
 *   pnpm build && pnpm bundle:report
 */

import { readFile, readdir, stat } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import path from "node:path";
import process from "node:process";

// Budgets in *gzipped* bytes — what actually crosses the wire.
// Current reality: JS ~127 KB / CSS ~8.4 KB gzip. Budgets leave
// headroom for organic growth; raising them is a conscious decision.
const JS_BUDGET_BYTES = 200 * 1024;
const CSS_BUDGET_BYTES = 50 * 1024;

const distDir = path.resolve(process.cwd(), "dist");

async function main() {
  try {
    await stat(path.join(distDir, "index.html"));
  } catch {
    console.error("dist/index.html not found — run `pnpm build` first.");
    process.exit(2);
  }

  const html = await readFile(path.join(distDir, "index.html"), "utf8");
  const refs = [...html.matchAll(/(?:src|href)="(\/?assets\/[^"]+)"/g)].map((m) =>
    m[1].replace(/^\/?assets\//, ""),
  );
  if (refs.length === 0) {
    console.error("No asset references found in dist/index.html — unexpected build shape.");
    process.exit(2);
  }

  let jsBytes = 0;
  let cssBytes = 0;
  const lines = [];
  for (const ref of refs) {
    const buf = await readFile(path.join(distDir, "assets", ref));
    const gz = gzipSync(buf).length;
    if (ref.endsWith(".js")) jsBytes += gz;
    else if (ref.endsWith(".css")) cssBytes += gz;
    lines.push(`  ${ref.padEnd(40)} ${fmt(buf.length)} raw  ${fmt(gz)} gzip`);
  }

  const all = await readdir(path.join(distDir, "assets"));
  let totalJs = 0;
  for (const f of all) {
    if (!f.endsWith(".js")) continue;
    totalJs += gzipSync(await readFile(path.join(distDir, "assets", f))).length;
  }

  console.log("First-screen assets (referenced directly by index.html):");
  console.log(lines.join("\n"));
  console.log(
    `\nFirst-screen JS: ${fmt(jsBytes)} gzip (budget ${fmt(JS_BUDGET_BYTES)})` +
      `\nFirst-screen CSS: ${fmt(cssBytes)} gzip (budget ${fmt(CSS_BUDGET_BYTES)})` +
      `\nAll lazy-loaded JS across ${all.filter((f) => f.endsWith(".js")).length} chunks: ${fmt(totalJs)} gzip` +
      `\n(mermaid diagram packs + shiki language packs + lazy panels — loaded on demand)`,
  );

  const failures = [];
  if (jsBytes > JS_BUDGET_BYTES) failures.push(`first-screen JS ${fmt(jsBytes)} > ${fmt(JS_BUDGET_BYTES)} gzip`);
  if (cssBytes > CSS_BUDGET_BYTES) failures.push(`first-screen CSS ${fmt(cssBytes)} > ${fmt(CSS_BUDGET_BYTES)} gzip`);
  if (failures.length > 0) {
    console.error(`\nBUNDLE BUDGET EXCEEDED:\n  ${failures.join("\n  ")}`);
    process.exit(1);
  }
  console.log("\nBundle budget OK.");
}

function fmt(n) {
  return `${(n / 1024).toFixed(1)} KB`;
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
