import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PermissionPatchPreview } from "../PermissionPatchPreview";
import type { PatchFile, PatchLine } from "../../types/ipc";

function line(kind: PatchLine["kind"], content: string): PatchLine {
  return {
    kind,
    old_line: null,
    new_line: null,
    content,
  };
}

function patchFile(path: string, lines: PatchLine[]): PatchFile {
  return {
    path,
    old_path: path,
    new_path: path,
    status: "modified",
    additions: lines.filter((item) => item.kind === "add").length,
    deletions: lines.filter((item) => item.kind === "delete").length,
    binary: false,
    hunks: [
      {
        old_start: 1,
        old_lines: lines.length,
        new_start: 1,
        new_lines: lines.length,
        header: "@@ -1 +1 @@",
        lines,
      },
    ],
  };
}

describe("PermissionPatchPreview", () => {
  it("keeps large permission diffs compact but lets the user inspect the full patch", () => {
    const longLines = Array.from({ length: 12 }, (_, index) =>
      line("add", index === 11 ? "UNIQUE_TAIL_LINE" : `line ${index}`),
    );
    const files = [
      patchFile("a.ts", longLines),
      patchFile("b.ts", [line("add", "b")]),
      patchFile("c.ts", [line("add", "c")]),
      patchFile("d.ts", [line("add", "UNIQUE_FOURTH_FILE")]),
    ];

    render(<PermissionPatchPreview files={files} />);

    expect(screen.getByText("4 files")).toBeInTheDocument();
    expect(screen.queryByText("+UNIQUE_TAIL_LINE")).not.toBeInTheDocument();
    expect(screen.queryByText("d.ts")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show full patch" }));

    expect(screen.getByText("+UNIQUE_TAIL_LINE")).toBeInTheDocument();
    expect(screen.getByText("d.ts")).toBeInTheDocument();
    expect(screen.getByText("+UNIQUE_FOURTH_FILE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
  });
});
