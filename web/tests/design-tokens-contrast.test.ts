/**
 * R38 — WCAG AA contrast audit for the design tokens in src/index.css.
 *
 * Parses both theme blocks (:root dark, :root.light) straight from the
 * stylesheet and recomputes WCAG 2.1 contrast ratios, so a token tweak
 * that silently drops below AA fails CI instead of shipping.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

function parseTokens(block: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  const re = /--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(block)) !== null) {
    tokens[m[1]] = m[2].toLowerCase();
  }
  return tokens;
}

const lightStart = css.indexOf(":root.light");
const darkTokens = parseTokens(css.slice(0, lightStart));
const lightTokens = parseTokens(css.slice(lightStart, css.indexOf(":root.light ::-webkit")));

/** WCAG 2.1 relative luminance of a #rrggbb color. */
function luminance(hex: string): number {
  const n = hex.slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(fg: string, bg: string): number {
  const [l1, l2] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (l1 + 0.05) / (l2 + 0.05);
}

const AA_TEXT = 4.5;

/**
 * Foreground × background pairs that carry readable text (body copy,
 * timestamps, links, status messages). 11-13px UI text cannot claim the
 * large-text 3:1 concession, so everything here must clear 4.5:1.
 */
const TEXT_PAIRS: Array<[fg: string, bg: string]> = [
  // primary / secondary text across the surfaces it sits on
  ["ink-0", "surface-0"],
  ["ink-0", "surface-1"],
  ["ink-0", "surface-2"],
  ["ink-0", "surface-3"],
  ["ink-1", "surface-0"],
  ["ink-1", "surface-1"],
  ["ink-1", "surface-2"],
  ["ink-1", "surface-3"],
  // tertiary text (timestamps, placeholders) — informational, not decorative
  ["ink-2", "surface-0"],
  ["ink-2", "surface-1"],
  ["ink-2", "surface-2"],
  // accent used as link/label text, including interactive shades
  ["accent", "surface-0"],
  ["accent", "surface-1"],
  ["accent", "surface-2"],
  ["accent-hover", "surface-0"],
  ["accent-hover", "surface-1"],
  ["accent-hover", "surface-2"],
  ["accent-active", "surface-0"],
  ["accent-active", "surface-1"],
  ["accent-active", "surface-2"],
  // text on a filled accent button
  ["accent-contrast", "accent"],
  // status text on the two surfaces it most often appears on
  ["status-error", "surface-0"],
  ["status-error", "surface-2"],
  ["status-warning", "surface-0"],
  ["status-warning", "surface-2"],
  ["status-success", "surface-0"],
  ["status-success", "surface-2"],
  ["status-info", "surface-0"],
  ["status-info", "surface-2"],
];

describe.each([
  ["dark", darkTokens],
  ["light", lightTokens],
])("design token contrast (%s theme)", (theme, tokens) => {
  it("parses the full token set from index.css", () => {
    // Guard against silently scanning the wrong file / an empty block.
    for (const name of [
      "surface-0",
      "surface-1",
      "surface-2",
      "surface-3",
      "ink-0",
      "ink-1",
      "ink-2",
      "accent",
      "accent-hover",
      "accent-active",
      "accent-contrast",
      "status-error",
      "status-warning",
      "status-success",
      "status-info",
    ]) {
      expect(tokens[name], `${theme} theme must define --${name}`).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it.each(TEXT_PAIRS)("%s on %s meets WCAG AA (4.5:1)", (fg, bg) => {
    const ratio = contrast(tokens[fg], tokens[bg]);
    expect(
      ratio,
      `${theme}: --${fg} ${tokens[fg]} on --${bg} ${tokens[bg]} = ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(AA_TEXT);
  });
});
