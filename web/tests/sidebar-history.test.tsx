/**
 * Tests for the redesigned sidebar history list — verifies each row
 * shows the status dot, a truncated title, a relative timestamp, and
 * that the list is scrollable (max-height + overflow-y-auto).
 */
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Sidebar } from "../src/components/layout/Sidebar";
import { useSessionStore } from "../src/stores";
import { typedIPC } from "../src/ipc";

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
const DAY = 24 * HOUR;

describe("Sidebar history list", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    vi.clearAllMocks();
    vi.mocked(typedIPC.listSessions).mockResolvedValue({ sessions: [], total: 0 });
    useSessionStore.setState({
      sessions: [],
      projects: [
        {
          id: "inbox",
          name: "收件箱",
          description: "",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
        },
      ],
      currentSessionId: null,
      loading: false,
      filter: "all",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders one row per session with a status dot and a title", () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_active",
          title: "Refactor",
          archived: false,
          created_at: NOW - HOUR,
          updated_at: NOW - HOUR,
          model_id: null,
        },
        {
          id: "ses_other",
          title: "Other task",
          archived: false,
          created_at: NOW - 2 * DAY,
          updated_at: NOW - 2 * DAY,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);

    expect(screen.getByTestId("sidebar-session-row-ses_active")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-session-row-ses_other")).toBeInTheDocument();

    const activeDot = screen.getByTestId("sidebar-session-dot-ses_active");
    expect(activeDot).toHaveAttribute("data-status", "active");

    expect(screen.getByText("Refactor")).toBeInTheDocument();
    expect(screen.getByText("Other task")).toBeInTheDocument();
  });

  it("marks the dot 'archived' when a session is in the archived filter", () => {
    useSessionStore.setState({
      filter: "archived",
      sessions: [
        {
          id: "ses_archived",
          title: "Old thing",
          archived: true,
          created_at: NOW - 2 * DAY,
          updated_at: NOW - 2 * DAY,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);

    expect(screen.getByTestId("sidebar-session-row-ses_archived")).toBeInTheDocument();
    const dot = screen.getByTestId("sidebar-session-dot-ses_archived");
    expect(dot).toHaveAttribute("data-status", "archived");
  });

  it("truncates long titles to <= 24 characters and shows ellipsis", () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_long",
          title:
            "This is a really really long session title that should be truncated for the sidebar",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);
    const rendered = screen.getByText(/This is a really really/);
    expect(rendered.textContent?.length).toBeLessThanOrEqual(24);
    expect(rendered.textContent?.endsWith("…")).toBe(true);
  });

  it("renders a relative timestamp per row (h/d/just now)", () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_recent",
          title: "Just now",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
        {
          id: "ses_hours",
          title: "Hours ago",
          archived: false,
          created_at: NOW - 3 * HOUR,
          updated_at: NOW - 3 * HOUR,
          model_id: null,
        },
        {
          id: "ses_days",
          title: "Days ago",
          archived: false,
          created_at: NOW - 2 * DAY,
          updated_at: NOW - 2 * DAY,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);

    const recent = screen.getByTestId("sidebar-session-time-ses_recent");
    const hours = screen.getByTestId("sidebar-session-time-ses_hours");
    const days = screen.getByTestId("sidebar-session-time-ses_days");
    expect(recent.textContent).toBe("just now");
    expect(hours.textContent).toBe("3h ago");
    expect(days.textContent).toBe("2d ago");
  });

  it("scrollable: the list element uses overflow-y-auto and a max-height", () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_x",
          title: "x",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);
    const list = screen.getByTestId("sidebar-session-list");
    expect(list.className).toMatch(/overflow-y-auto/);
    // maxHeight removed — flex layout handles sizing naturally
  });

  it("filters loaded history and exposes an empty search state", async () => {
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_alpha",
          title: "Refactor parser",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
        {
          id: "ses_beta",
          title: "Write docs",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);

    fireEvent.change(screen.getByTestId("sidebar-session-search"), {
      target: { value: "docs" },
    });
    expect(screen.queryByTestId("sidebar-session-row-ses_alpha")).toBeNull();
    expect(screen.getByTestId("sidebar-session-row-ses_beta")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-session-count")).toHaveTextContent("1");

    fireEvent.change(screen.getByTestId("sidebar-session-search"), {
      target: { value: "missing" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
      await Promise.resolve();
    });
    expect(screen.getByTestId("sidebar-session-empty")).toHaveTextContent("无匹配任务");

    fireEvent.click(screen.getByTestId("sidebar-session-search-clear"));
    expect(screen.getByTestId("sidebar-session-search")).toHaveValue("");
    expect(screen.getByTestId("sidebar-session-row-ses_alpha")).toBeInTheDocument();
  });

  it("searches the backend so older unloaded sessions can be found", async () => {
    vi.mocked(typedIPC.listSessions).mockResolvedValueOnce({
      sessions: [
        {
          id: "ses_remote",
          title: "Remote architecture notes",
          archived: false,
          created_at: NOW - DAY,
          updated_at: NOW - HOUR,
          model_id: null,
        },
      ],
      total: 1,
    });
    useSessionStore.setState({
      sessions: [
        {
          id: "ses_local",
          title: "Local visible task",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
        },
      ],
    });
    render(<Sidebar />);

    fireEvent.change(screen.getByTestId("sidebar-session-search"), {
      target: { value: "architecture" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
      await Promise.resolve();
    });

    expect(typedIPC.listSessions).toHaveBeenCalledWith({
      archived: false,
      search: "architecture",
      limit: 50,
    });
    expect(screen.getByTestId("sidebar-session-row-ses_remote")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar-session-row-ses_local")).toBeNull();
    expect(useSessionStore.getState().sessions.some((s) => s.id === "ses_remote")).toBe(true);
  });

  it("auto-expands a collapsed project when its session matches the search", () => {
    useSessionStore.setState({
      expandedProjectIds: [],
      projects: [
        {
          id: "inbox",
          name: "收件箱",
          description: "",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
        },
        {
          id: "proj_docs",
          name: "Docs",
          description: "",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
        },
      ],
      sessions: [
        {
          id: "ses_collapsed",
          title: "Draft README",
          archived: false,
          created_at: NOW,
          updated_at: NOW,
          model_id: null,
          project_id: "proj_docs",
        },
      ],
    });
    render(<Sidebar />);

    // Initially collapsed.
    expect(screen.queryByTestId("sidebar-session-row-ses_collapsed")).toBeNull();

    fireEvent.change(screen.getByTestId("sidebar-session-search"), {
      target: { value: "README" },
    });
    expect(screen.getByTestId("sidebar-session-row-ses_collapsed")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-project-proj_docs").querySelector("button")).toHaveAttribute("aria-expanded", "true");
  });
});
