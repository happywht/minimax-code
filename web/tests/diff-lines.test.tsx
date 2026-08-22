/**
 * Tests for the shared colour-coded diff renderer (P2-4) — unified
 * diff lines get per-line tones (+/green, -/red, @@/info, headers),
 * replacing the checkpoint panel's plain-text <pre>.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

/** All rendered diff rows, keyed by their exact text. */
function rowsByText(text: string): Record<string, HTMLElement> {
  const container = render(<DiffLines text={text} />).container;
  const pre = container.querySelector("pre") as HTMLElement;
  const map: Record<string, HTMLElement> = {};
  pre.querySelectorAll("div").forEach((el) => {
    map[el.textContent ?? ""] = el as HTMLElement;
  });
  return map;
}
import { DiffLines } from "../src/ui/DiffLines";

const SAMPLE = [
  "diff --git a/a.ts b/a.ts",
  "--- a/a.ts",
  "+++ b/a.ts",
  "@@ -1,3 +1,4 @@",
  " context line",
  "-removed line",
  "+added line",
].join("\n");

describe("DiffLines (P2-4)", () => {
  it("colours additions, deletions, hunk markers and file headers", () => {
    const byText = rowsByText(SAMPLE);

    // + additions and - deletions carry the success/error tones.
    expect(byText["+added line"].className).toContain("text-status-success");
    expect(byText["-removed line"].className).toContain("text-status-error");
    // Hunk markers and file headers are info/accent, not line-toned.
    expect(byText["@@ -1,3 +1,4 @@"].className).toContain("text-status-info");
    expect(byText["--- a/a.ts"].className).toContain("text-accent");
    // Context lines stay neutral.
    expect(byText[" context line"].className).not.toContain("text-status-success");
    expect(byText[" context line"].className).not.toContain("text-status-error");
  });

  it("renders one row per line and preserves the text verbatim", () => {
    const byText = rowsByText(SAMPLE);
    expect(Object.keys(byText)).toHaveLength(SAMPLE.split("\n").length);
    expect(byText["+added line"]).toBeTruthy();
  });

  it("shows the localised empty state when the diff is blank", () => {
    render(<DiffLines text="" emptyText="暂无差异" />);
    expect(screen.getByText("暂无差异")).toBeInTheDocument();
  });
});
