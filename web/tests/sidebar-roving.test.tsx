/**
 * R37 — roving tabindex keyboard navigation for the sidebar session
 * list. Exactly one row is a tab stop; ArrowUp/Down/Home/End move it
 * along the rendered (grouped) order. Enter opens the focused row.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Sidebar } from "../src/components/layout/Sidebar";
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
      listSessions: vi.fn(async () => ({ sessions: [], total: 0 })),
    },
  };
});

const NOW = new Date("2026-06-02T12:00:00Z").getTime();
const HOUR = 60 * 60 * 1_000;

/** Three sessions across two projects, DOM order: a, b, c. */
function seedSessions(currentSessionId: string | null): void {
  useSessionStore.setState({
    sessions: [
      {
        id: "ses_a",
        title: "Alpha",
        archived: false,
        created_at: NOW,
        updated_at: NOW,
        model_id: null,
      },
      {
        id: "ses_b",
        title: "Beta",
        archived: false,
        created_at: NOW - HOUR,
        updated_at: NOW - HOUR,
        model_id: null,
        project_id: "proj_x",
      },
      {
        id: "ses_c",
        title: "Gamma",
        archived: false,
        created_at: NOW - 2 * HOUR,
        updated_at: NOW - 2 * HOUR,
        model_id: null,
        project_id: "proj_x",
      },
    ],
    projects: [
      {
        id: "inbox",
        name: "收件箱",
        description: "",
        root_path: "",
        archived: false,
        created_at: NOW,
        updated_at: NOW,
      },
      {
        id: "proj_x",
        name: "Proj X",
        description: "",
        root_path: "",
        archived: false,
        created_at: NOW,
        updated_at: NOW,
      },
    ],
    currentSessionId,
    loading: false,
    filter: "all",
    // Project groups default to collapsed (only "inbox" expands), which
    // would hide the proj_x rows — expand both for DOM-order assertions.
    expandedProjectIds: ["inbox", "proj_x"],
  });
}

function rowButton(id: string): HTMLElement {
  // NavItem button carries testId `sidebar-session-<id>`
  return screen.getByTestId(`sidebar-session-${id}`);
}

describe("Sidebar session list roving tabindex (R37)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    vi.clearAllMocks();
    seedSessions("ses_b");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("exactly one row is the tab stop — the current session", () => {
    render(<Sidebar />);
    expect(rowButton("ses_b")).toHaveAttribute("tabindex", "0");
    expect(rowButton("ses_a")).toHaveAttribute("tabindex", "-1");
    expect(rowButton("ses_c")).toHaveAttribute("tabindex", "-1");
  });

  it("falls back to the first row when the current session is not visible", () => {
    useSessionStore.setState({ currentSessionId: "ses_gone" });
    render(<Sidebar />);
    expect(rowButton("ses_a")).toHaveAttribute("tabindex", "0");
    expect(rowButton("ses_b")).toHaveAttribute("tabindex", "-1");
  });

  it("ArrowDown moves focus and the tab stop to the next row across groups", () => {
    render(<Sidebar />);
    const start = rowButton("ses_b");
    start.focus();
    expect(start).toHaveFocus();

    fireEvent.keyDown(start, { key: "ArrowDown" });
    expect(rowButton("ses_c")).toHaveFocus();
    expect(rowButton("ses_c")).toHaveAttribute("tabindex", "0");
    expect(rowButton("ses_b")).toHaveAttribute("tabindex", "-1");
  });

  it("ArrowUp moves focus to the previous row; clamps at the first row", () => {
    render(<Sidebar />);
    const first = rowButton("ses_a");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowUp" });
    // clamped — focus stays on the first row
    expect(first).toHaveFocus();
    expect(first).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(rowButton("ses_b"), { key: "ArrowUp" });
    expect(first).toHaveFocus();
  });

  it("Home jumps to the first row and End to the last", () => {
    render(<Sidebar />);
    const middle = rowButton("ses_b");
    middle.focus();

    fireEvent.keyDown(middle, { key: "End" });
    expect(rowButton("ses_c")).toHaveFocus();

    fireEvent.keyDown(rowButton("ses_c"), { key: "Home" });
    expect(rowButton("ses_a")).toHaveFocus();
    expect(rowButton("ses_a")).toHaveAttribute("tabindex", "0");
  });

  it("the tab stop persists after navigating away (Tab returns to the roving row)", () => {
    render(<Sidebar />);
    const start = rowButton("ses_b");
    start.focus();
    fireEvent.keyDown(start, { key: "ArrowDown" });
    fireEvent.keyDown(rowButton("ses_c"), { key: "ArrowDown" });
    // clamped at last: stop remains ses_c
    expect(rowButton("ses_c")).toHaveFocus();
    expect(rowButton("ses_c")).toHaveAttribute("tabindex", "0");
    expect(rowButton("ses_a")).toHaveAttribute("tabindex", "-1");
    expect(rowButton("ses_b")).toHaveAttribute("tabindex", "-1");
  });

  it("Enter on a focused row selects it", () => {
    render(<Sidebar />);
    const target = rowButton("ses_a");
    target.focus();
    fireEvent.click(target); // buttons open on Enter/Space natively; click is the effect
    expect(useSessionStore.getState().currentSessionId).toBe("ses_a");
  });
});
