import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PatchPreviewPanel } from "../src/components/panels/PatchPreviewPanel";
import { usePatchPreviewStore } from "../src/stores";
import type { PatchPreviewResult } from "../src/types/ipc";

const PREVIEW: PatchPreviewResult = {
  scope: "working",
  ref: null,
  diff: "diff --git a/app.ts b/app.ts\n",
  stats: { files: 1, additions: 2, deletions: 1 },
  files: [
    {
      path: "app.ts",
      old_path: "app.ts",
      new_path: "app.ts",
      status: "modified",
      additions: 2,
      deletions: 1,
      binary: false,
      hunks: [
        {
          old_start: 1,
          old_lines: 2,
          new_start: 1,
          new_lines: 3,
          header: "",
          lines: [
            { kind: "context", old_line: 0, new_line: 0, content: "function answer() {" },
            { kind: "delete", old_line: 1, new_line: null, content: "old line" },
            { kind: "add", old_line: null, new_line: 1, content: "new line" },
            { kind: "add", old_line: null, new_line: 2, content: "another line" },
            { kind: "add", old_line: null, new_line: 3, content: "line 3" },
            { kind: "add", old_line: null, new_line: 4, content: "line 4" },
            { kind: "add", old_line: null, new_line: 5, content: "line 5" },
            { kind: "add", old_line: null, new_line: 6, content: "line 6" },
            { kind: "add", old_line: null, new_line: 7, content: "line 7" },
            { kind: "add", old_line: null, new_line: 8, content: "line 8" },
            { kind: "add", old_line: null, new_line: 9, content: "UNIQUE_FULL_HUNK_TAIL" },
          ],
        },
      ],
    },
  ],
};

vi.mock("../src/ipc", () => ({
  typedIPC: {
    patchPreview: vi.fn(async () => PREVIEW),
    patchApplyHunk: vi.fn(async () => ({
      ok: true,
      operation: "apply_hunk",
      scope: "working",
      file_path: "app.ts",
      hunk_index: 0,
    })),
    patchRevertHunk: vi.fn(async () => ({
      ok: true,
      operation: "revert_hunk",
      scope: "working",
      file_path: "app.ts",
      hunk_index: 0,
    })),
  },
}));

describe("PatchPreviewPanel", () => {
  beforeEach(() => {
    usePatchPreviewStore.getState().reset();
  });

  it("loads and renders structured diff stats and file preview", async () => {
    render(<PatchPreviewPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("patch-file-card-app.ts")).toBeInTheDocument();
    });
    expect(screen.getByTestId("patch-preview-panel-stats")).toHaveTextContent("1 file");
    expect(screen.getByTestId("patch-preview-panel-stats")).toHaveTextContent("+2");
    expect(screen.getByTestId("patch-preview-panel-stats")).toHaveTextContent("-1");
    expect(screen.getByText("+new line")).toBeInTheDocument();
    expect(screen.getByText("-old line")).toBeInTheDocument();
  });

  it("keeps long hunks compact but lets users inspect the complete hunk", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toBeInTheDocument();
    });

    expect(screen.queryByText(/function answer/)).not.toBeInTheDocument();
    expect(screen.queryByText("+UNIQUE_FULL_HUNK_TAIL")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show full hunk" }));

    expect(screen.getByText(/function answer/)).toBeInTheDocument();
    expect(screen.getByText("+UNIQUE_FULL_HUNK_TAIL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
  });

  it("jumps from the file overview to the selected diff card", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-file-jump-app.ts")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("patch-preview-panel-file-jump-app.ts"));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" });
    expect(screen.getByTestId("patch-file-card-app.ts").className).toContain("ring-1");
  });

  it("tracks approve, reject, and reset decisions per hunk", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("patch-hunk-app.ts-0-1-1-approve"));
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toHaveAttribute("data-decision", "approved");
    });
    expect(screen.getByText("approved")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("patch-hunk-app.ts-0-1-1-reject"));
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toHaveAttribute("data-decision", "rejected");
    });
    expect(screen.getByText("rejected")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("patch-hunk-app.ts-0-1-1-reset"));
    expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toHaveAttribute("data-decision", "pending");
  });

  it("clears hunk decisions when the preview refreshes", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("patch-hunk-app.ts-0-1-1-approve"));
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toHaveAttribute("data-decision", "approved");
    });

    fireEvent.click(screen.getByTestId("patch-preview-panel-refresh"));
    await waitFor(() => {
      expect(screen.getByTestId("patch-hunk-app.ts-0-1-1")).toHaveAttribute("data-decision", "pending");
    });
  });
});
