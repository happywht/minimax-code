/**
 * Tests for the git store.
 *
 * The store wraps the ``git.*`` IPC namespace in a zustand slice
 * and exposes ``refreshStatus()``, ``fetchDiff()``, and
 * ``fetchLog()``. We assert on the resulting state shape and
 * verify error paths surface a toast (so the UI can show a
 * "couldn't read git" message rather than silently failing).
 *
 * The status call is intentionally silent on failure — the top-
 * bar widget polls it on a timer and toasting on every poll
 * would spam the user. Diff and log calls toast on failure
 * because they're user-initiated.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useGitStore } from "../src/stores";

const noToast = () => {
  // Empty stub for the error boundary's `toast` — see the
  // workspace-switcher test for the same pattern.
};

vi.mock("../src/components/ErrorBoundary", () => ({
  toast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
  },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      gitStatus: vi.fn(),
      gitDiff: vi.fn(),
      gitLog: vi.fn(),
    },
  };
});

beforeEach(() => {
  useGitStore.setState({ status: null, lastDiff: null, lastLog: null, loading: false });
  noToast();
});

describe("useGitStore", () => {
  it("starts with no cached data", () => {
    const s = useGitStore.getState();
    expect(s.status).toBeNull();
    expect(s.lastDiff).toBeNull();
    expect(s.lastLog).toBeNull();
    expect(s.loading).toBe(false);
  });

  it("refreshStatus populates status on success", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValueOnce({
      branch: "main",
      clean: false,
      ahead: 0,
      behind: 0,
      modified: ["foo.ts"],
      untracked: [],
      staged: [],
    });
    await useGitStore.getState().refreshStatus();
    const s = useGitStore.getState();
    expect(s.status?.branch).toBe("main");
    expect(s.status?.modified).toEqual(["foo.ts"]);
    expect(s.loading).toBe(false);
  });

  it("refreshStatus is silent on failure (no toast)", async () => {
    const { typedIPC } = await import("../src/ipc");
    const toastMod = await import("../src/components/ErrorBoundary");
    vi.mocked(typedIPC.gitStatus).mockRejectedValueOnce(new Error("network down"));
    await useGitStore.getState().refreshStatus();
    expect(toastMod.toast.error).not.toHaveBeenCalled();
    // Loading flag should reset even on failure.
    expect(useGitStore.getState().loading).toBe(false);
  });

  it("fetchDiff stores the diff and echoes the scope", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitDiff).mockResolvedValueOnce({
      diff: "+new line\n",
      scope: "working",
    });
    const result = await useGitStore.getState().fetchDiff({ scope: "working" });
    expect(result?.diff).toBe("+new line\n");
    expect(useGitStore.getState().lastDiff?.scope).toBe("working");
  });

  it("fetchDiff toasts on failure", async () => {
    const { typedIPC } = await import("../src/ipc");
    const toastMod = await import("../src/components/ErrorBoundary");
    vi.mocked(typedIPC.gitDiff).mockRejectedValueOnce(new Error("bad ref"));
    const result = await useGitStore.getState().fetchDiff({ ref: "wat" });
    expect(result).toBeNull();
    expect(toastMod.toast.error).toHaveBeenCalled();
  });

  it("fetchLog stores the entries", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitLog).mockResolvedValueOnce({
      entries: [
        { sha: "abc123", author: "test", message: "first", files_changed: ["a.txt"] },
      ],
    });
    const result = await useGitStore.getState().fetchLog({ n: 5 });
    expect(result?.entries.length).toBe(1);
    expect(result?.entries[0].sha).toBe("abc123");
    expect(useGitStore.getState().lastLog?.entries.length).toBe(1);
  });

  it("fetchLog toasts on failure", async () => {
    const { typedIPC } = await import("../src/ipc");
    const toastMod = await import("../src/components/ErrorBoundary");
    vi.mocked(typedIPC.gitLog).mockRejectedValueOnce(new Error("timeout"));
    const result = await useGitStore.getState().fetchLog();
    expect(result).toBeNull();
    expect(toastMod.toast.error).toHaveBeenCalled();
  });

  it("reset clears all cached snapshots", async () => {
    useGitStore.setState({
      status: { branch: "x", clean: true, ahead: 0, behind: 0, modified: [], untracked: [], staged: [] },
      lastDiff: { diff: "d", scope: "s" },
      lastLog: { entries: [] },
      loading: false,
    });
    useGitStore.getState().reset();
    const s = useGitStore.getState();
    expect(s.status).toBeNull();
    expect(s.lastDiff).toBeNull();
    expect(s.lastLog).toBeNull();
  });
});
