/**
 * Tests for PatchPreviewPanel — the Patch Studio diff inspector.
 *
 * We mock the typedIPC layer so the panel renders with a deterministic
 * structured diff and the hunk/file/global action handlers resolve
 * synchronously.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PatchPreviewPanel } from "../PatchPreviewPanel";
import { usePatchPreviewStore } from "../../../stores";
import type { PatchPreviewResult } from "../../../types/ipc";

vi.mock("../../../ipc", async () => {
  const actual = await vi.importActual<typeof import("../../../ipc")>("../../../ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      patchPreview: vi.fn(async () => SAMPLE_DIFF),
      patchApplyHunk: vi.fn(async () => ({ ok: true, operation: "apply_hunk", scope: "working", file_path: "a.py", hunk_index: 0 } as const)),
      patchRevertHunk: vi.fn(async () => ({ ok: true, operation: "revert_hunk", scope: "working", file_path: "a.py", hunk_index: 0 } as const)),
      patchApplyFile: vi.fn(async () => ({ ok: true, operation: "apply_file", scope: "working", file_path: "a.py" } as const)),
      patchRevertFile: vi.fn(async () => ({ ok: true, operation: "revert_file", scope: "working", file_path: "a.py" } as const)),
      patchApplyAll: vi.fn(async () => ({ ok: true, operation: "apply_all", scope: "working", applied: ["a.py"], failed: [] } as const)),
      patchRevertAll: vi.fn(async () => ({ ok: true, operation: "revert_all", scope: "working", applied: ["a.py"], failed: [] } as const)),
      patchSaveSnapshot: vi.fn(async () => ({ ok: true, snapshot_ref: "stash-ref", clean: false } as const)),
    },
  };
});

vi.mock("../../layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

const SAMPLE_DIFF: PatchPreviewResult = {
  scope: "working",
  ref: null,
  diff: "",
  stats: { files: 1, additions: 1, deletions: 1 },
  files: [
    {
      path: "a.py",
      old_path: "a.py",
      new_path: "a.py",
      status: "modified",
      additions: 1,
      deletions: 1,
      binary: false,
      hunks: [
        {
          old_start: 1,
          old_lines: 2,
          new_start: 1,
          new_lines: 2,
          header: "",
          lines: [
            { kind: "context", old_line: 1, new_line: 1, content: "def a():" },
            { kind: "delete", old_line: 2, new_line: null, content: "    return 1" },
            { kind: "add", old_line: null, new_line: 2, content: "    return 2" },
          ],
        },
      ],
    },
  ],
};

beforeEach(() => {
  usePatchPreviewStore.getState().reset();
});

describe("PatchPreviewPanel", () => {
  it("renders the diff scope switcher and file list", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-files")).toBeInTheDocument();
    });
    expect(screen.getByTestId("patch-preview-panel-scope-working")).toBeInTheDocument();
    expect(screen.getByTestId("patch-preview-panel-scope-staged")).toBeInTheDocument();
    expect(screen.getByTestId("patch-preview-panel-scope-branch")).toBeInTheDocument();
    expect(screen.getByTestId("patch-file-card-a.py")).toBeInTheDocument();
  });

  it("shows global action buttons", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-files")).toBeInTheDocument();
    });
    expect(screen.getByTestId("patch-preview-panel-snapshot")).toBeInTheDocument();
    expect(screen.getByTestId("patch-preview-panel-apply-all")).toBeInTheDocument();
    expect(screen.getByTestId("patch-preview-panel-revert-all")).toBeInTheDocument();
  });

  it("enables only the snapshot button in branch scope", async () => {
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-files")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("patch-preview-panel-scope-branch"));
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-apply-all")).toBeDisabled();
      expect(screen.getByTestId("patch-preview-panel-revert-all")).toBeDisabled();
      expect(screen.getByTestId("patch-preview-panel-snapshot")).not.toBeDisabled();
    });
  });

  it("calls applyAll when the apply-all button is clicked", async () => {
    const { typedIPC } = await import("../../../ipc");
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-files")).toBeInTheDocument();
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("patch-preview-panel-apply-all"));
    });
    await waitFor(() => {
      expect(typedIPC.patchApplyAll).toHaveBeenCalledWith({ scope: "working" });
    });
  });

  it("calls revertFile when a file-level reject button is clicked", async () => {
    const { typedIPC } = await import("../../../ipc");
    render(<PatchPreviewPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("patch-preview-panel-files")).toBeInTheDocument();
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("patch-file-card-a.py-reject"));
    });
    await waitFor(() => {
      expect(typedIPC.patchRevertFile).toHaveBeenCalledWith({ scope: "working", file_path: "a.py" });
    });
  });
});
