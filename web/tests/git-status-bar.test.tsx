/**
 * Tests for the GitStatusBar widget.
 *
 * The widget is a thin read-only view of the git store. We
 * drive the store directly (via ``useGitStore.setState``) rather
 * than mocking the IPC layer — the store tests already cover
 * the IPC round-trip; the component test's job is to verify the
 * rendered DOM matches the store snapshot.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { GitStatusBar } from "../src/components/GitStatusBar";
import { useGitStore } from "../src/stores";

vi.mock("../src/components/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

// Mock the typedIPC so the store's refreshStatus() resolves
// (or rejects) without hitting the network. The default
// implementation here returns a benign "main + clean" status;
// per-test overrides are applied via mockResolvedValueOnce in
// the failing-fetch scenarios.
vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      gitStatus: vi.fn(async () => ({
        branch: "main",
        clean: true,
        ahead: 0,
        behind: 0,
        modified: [],
        untracked: [],
        staged: [],
      })),
      gitDiff: vi.fn(async () => ({ diff: "", scope: "working" })),
      gitLog: vi.fn(async () => ({ entries: [] })),
    },
  };
});

beforeEach(async () => {
  useGitStore.setState({ status: null, lastDiff: null, lastLog: null, loading: false });
  // Reset the default gitStatus mock — a previous test may
  // have overridden it with mockResolvedValueOnce.
  const { typedIPC } = await import("../src/ipc");
  vi.mocked(typedIPC.gitStatus).mockReset();
  vi.mocked(typedIPC.gitStatus).mockResolvedValue({
    branch: "main",
    clean: true,
    ahead: 0,
    behind: 0,
    modified: [],
    untracked: [],
    staged: [],
  });
});

describe("GitStatusBar", () => {
  it("renders the trigger button with a git-status-bar test id", async () => {
    render(<GitStatusBar />);
    expect(screen.getByTestId("git-status-bar")).toBeInTheDocument();
    expect(screen.getByTestId("git-status-bar-trigger")).toBeInTheDocument();
    // Wait for the mount-driven refresh to complete so the
    // next-test state is clean.
    await waitFor(() => {
      expect(useGitStore.getState().status).not.toBeNull();
    });
  });

  it("shows the branch name and a clean indicator when status is clean", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: true,
      ahead: 0,
      behind: 0,
      modified: [],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    // Wait for the mount-driven refresh to populate the store.
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-branch")).toHaveTextContent("main");
    });
    expect(screen.getByTestId("git-status-bar-indicator")).toHaveTextContent("clean");
    expect(screen.getByTestId("git-status-bar-icon-clean")).toBeInTheDocument();
  });

  it("shows a dirty indicator with the change count when there are modifications", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "feature",
      clean: false,
      ahead: 0,
      behind: 0,
      modified: ["foo.ts", "bar.ts"],
      untracked: ["scratch.txt"],
      staged: ["a.txt"],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-branch")).toHaveTextContent("feature");
    });
    expect(screen.getByTestId("git-status-bar-indicator")).toHaveTextContent("4 changes");
    expect(screen.getByTestId("git-status-bar-icon-dirty")).toBeInTheDocument();
  });

  it("uses singular 'change' for a single modification", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: false,
      ahead: 0,
      behind: 0,
      modified: ["foo.ts"],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-indicator")).toHaveTextContent("1 change");
    });
  });

  it("opens the popover on click and lists dirty files", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: false,
      ahead: 0,
      behind: 0,
      modified: ["foo.ts"],
      untracked: ["scratch.txt"],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-indicator")).toHaveTextContent("2 changes");
    });
    expect(screen.queryByTestId("git-status-bar-popover")).toBeNull();
    fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    const popover = await screen.findByTestId("git-status-bar-popover");
    expect(popover).toBeInTheDocument();
    // The dirty files should be listed.
    expect(screen.getByTestId("git-status-bar-modified-item")).toHaveTextContent("foo.ts");
    expect(screen.getByTestId("git-status-bar-untracked-item")).toHaveTextContent("scratch.txt");
  });

  it("renders a clean message when status.clean is true and popover opens", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: true,
      ahead: 0,
      behind: 0,
      modified: [],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-icon-clean")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    expect(await screen.findByTestId("git-status-bar-popover")).toBeInTheDocument();
    expect(screen.getByTestId("git-status-bar-clean-message")).toHaveTextContent(
      /clean/i,
    );
  });

  it("shows ahead/behind badges in the popover when present", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "feature",
      clean: true,
      ahead: 3,
      behind: 1,
      modified: [],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-branch")).toHaveTextContent("feature");
    });
    fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    expect(await screen.findByTestId("git-status-bar-ahead")).toHaveTextContent("3");
    expect(screen.getByTestId("git-status-bar-behind")).toHaveTextContent("1");
  });

  it("shows a loading state when status is null", () => {
    render(<GitStatusBar />);
    // Before the mount refresh resolves, the indicator shows
    // "loading…". We assert on the spinner specifically.
    expect(screen.getByTestId("git-status-bar-loading")).toBeInTheDocument();
    expect(screen.getByTestId("git-status-bar-indicator")).toHaveTextContent("loading");
  });

  it("clicking the trigger a second time closes the popover", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: true,
      ahead: 0,
      behind: 0,
      modified: [],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-icon-clean")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    expect(await screen.findByTestId("git-status-bar-popover")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    await waitFor(() => {
      expect(screen.queryByTestId("git-status-bar-popover")).toBeNull();
    });
  });

  it("opens the popover and triggers a fresh refresh on click", async () => {
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.gitStatus).mockResolvedValue({
      branch: "main",
      clean: true,
      ahead: 0,
      behind: 0,
      modified: [],
      untracked: [],
      staged: [],
    });
    render(<GitStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("git-status-bar-icon-clean")).toBeInTheDocument();
    });
    const before = vi.mocked(typedIPC.gitStatus).mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByTestId("git-status-bar-trigger"));
    });
    // At least one more call fired (the open-triggered refresh).
    expect(vi.mocked(typedIPC.gitStatus).mock.calls.length).toBeGreaterThan(before);
  });
});
