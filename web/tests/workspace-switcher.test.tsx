/**
 * Tests for the WorkspaceSwitcher dropdown.
 *
 * Covers:
 *   1. Renders the current workspace name on first mount (after
 *      localStorage is seeded).
 *   2. Clicking the trigger opens the dropdown and lists every
 *      workspace with a checkmark on the active one.
 *   3. Selecting another workspace updates localStorage, clears the
 *      session list, and calls `sessionStore.refresh()`.
 */
import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WorkspaceSwitcher } from "../src/components/layout/WorkspaceSwitcher";
import { useSessionStore } from "../src/stores";

vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listSessions: vi.fn(async () => ({ sessions: [] })),
    },
  };
});

function seedWorkspaces(workspaces: Array<{ name: string; path?: string }>) {
  localStorage.setItem(
    "minimax-code:workspaces",
    JSON.stringify(
      workspaces.map((w) => ({ name: w.name, path: w.path ?? "" })),
    ),
  );
  localStorage.setItem(
    "minimax-code:current-workspace",
    workspaces[0]?.name ?? "default",
  );
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    loading: false,
    filter: "all",
  });
});

afterEach(() => {
  localStorage.clear();
});

describe("WorkspaceSwitcher", () => {
  it("renders the current workspace name from localStorage", () => {
    seedWorkspaces([{ name: "alpha" }, { name: "beta", path: "/work/beta" }]);
    render(<WorkspaceSwitcher />);
    expect(screen.getByTestId("workspace-switcher")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent(
      "alpha",
    );
  });

  it("opens the dropdown and lists all workspaces with a checkmark on the active one", () => {
    seedWorkspaces([
      { name: "alpha" },
      { name: "beta", path: "/work/beta" },
      { name: "gamma" },
    ]);
    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    const menu = screen.getByTestId("workspace-switcher-menu");
    expect(menu).toBeInTheDocument();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[1]).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("workspace-switcher-check")).toBeInTheDocument();
  });

  it("switching a workspace persists selection, clears the session list, and triggers a refresh", async () => {
    seedWorkspaces([{ name: "alpha" }, { name: "beta" }]);
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_old",
          title: "old",
          archived: false,
          created_at: 0,
          updated_at: 0,
          model_id: null,
        },
      ],
      currentSessionId: "ses_old",
    });
    const refreshSpy = vi.fn(async () => {
      // Simulate the refresh wiping the previous session set so the
      // store ends up in a clean state for the new workspace.
      useSessionStore.setState({ sessions: [], currentSessionId: null });
    });
    useSessionStore.setState({ refresh: refreshSpy });

    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    fireEvent.click(screen.getByTestId("workspace-option-beta"));

    // Selection persisted to localStorage.
    expect(localStorage.getItem("minimax-code:current-workspace")).toBe(
      "beta",
    );
    // Label reflects the new workspace.
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent(
      "beta",
    );
    // Refresh was called.
    await waitFor(() => {
      expect(refreshSpy).toHaveBeenCalledTimes(1);
    });
    // Session list was cleared as part of the switch.
    expect(useSessionStore.getState().sessions).toEqual([]);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    // Dropdown closed after the selection.
    expect(screen.queryByTestId("workspace-switcher-menu")).toBeNull();
  });
});
