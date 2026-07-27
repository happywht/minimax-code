/**
 * Tests for SessionRow — sidebar session row interactions.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SessionRow } from "../SessionRow";
import { useSessionStore } from "../../../stores";
import type { SessionMeta } from "../../../stores";
import type { Project } from "../../../types/ipc";

const mockProjects: Project[] = [
  { id: "inbox", name: "收件箱", description: "", archived: false, created_at: 0, updated_at: 10 },
  { id: "p1", name: "Project One", description: "", archived: false, created_at: 0, updated_at: 20 },
];

function makeSession(overrides: Partial<SessionMeta> = {}): SessionMeta {
  return {
    id: "s1",
    title: "Test session",
    archived: false,
    project_id: "inbox",
    created_at: Date.now() - 60_000,
    updated_at: Date.now(),
    model_id: null,
    workspace_mode: "local",
    ...overrides,
  };
}

function renderRow(props: Partial<React.ComponentProps<typeof SessionRow>> = {}) {
  return render(
    <SessionRow
      session={makeSession()}
      selected={false}
      projects={mockProjects}
      onClick={vi.fn()}
      {...props}
    />,
  );
}

describe("SessionRow", () => {
  beforeEach(() => {
    useSessionStore.setState({
      archive: vi.fn(),
      unarchive: vi.fn(),
      remove: vi.fn(),
      rename: vi.fn(),
      moveSessionProject: vi.fn(),
    } as Partial<ReturnType<typeof useSessionStore.getState>> as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("renders session title and status dot", () => {
    renderRow();
    expect(screen.getByTestId("sidebar-session-s1")).toHaveTextContent("Test session");
    expect(screen.getByTestId("sidebar-session-dot-s1")).toBeInTheDocument();
  });

  it("calls onClick when the row is clicked", () => {
    const onClick = vi.fn();
    renderRow({ onClick });
    fireEvent.click(screen.getByTestId("sidebar-session-s1"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("opens rename input and commits on blur", () => {
    const rename = vi.fn();
    useSessionStore.setState({ rename } as Partial<ReturnType<typeof useSessionStore.getState>> as never);
    renderRow();
    fireEvent.click(screen.getByTestId("sidebar-session-menu-s1"));
    fireEvent.click(screen.getByText("重命名"));
    const input = screen.getByTestId("sidebar-session-rename-input-s1");
    expect(input).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "New title" } });
    fireEvent.blur(input);
    expect(rename).toHaveBeenCalledWith("s1", "New title");
  });

  it("archives an active session via the menu", () => {
    const archive = vi.fn();
    useSessionStore.setState({ archive } as Partial<ReturnType<typeof useSessionStore.getState>> as never);
    renderRow();
    fireEvent.click(screen.getByTestId("sidebar-session-menu-s1"));
    fireEvent.click(screen.getByText("归档"));
    expect(archive).toHaveBeenCalledWith("s1");
  });

  it("unarchives an archived session via the menu", () => {
    const unarchive = vi.fn();
    useSessionStore.setState({ unarchive } as Partial<ReturnType<typeof useSessionStore.getState>> as never);
    renderRow({ session: makeSession({ archived: true }) });
    fireEvent.click(screen.getByTestId("sidebar-session-menu-s1"));
    fireEvent.click(screen.getByText("取消归档"));
    expect(unarchive).toHaveBeenCalledWith("s1");
  });

  it("shows delete confirm modal and calls remove on confirm", () => {
    const remove = vi.fn();
    useSessionStore.setState({ remove } as Partial<ReturnType<typeof useSessionStore.getState>> as never);
    renderRow();
    fireEvent.click(screen.getByTestId("sidebar-session-menu-s1"));
    fireEvent.click(screen.getByText("删除"));
    expect(screen.getByText("删除任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(remove).toHaveBeenCalledWith("s1");
  });

  it("shows a checkbox in selection mode and toggles without navigating", () => {
    const onToggleSelect = vi.fn();
    const onClick = vi.fn();
    renderRow({ selectionActive: true, isSelected: false, onToggleSelect, onClick });
    const checkbox = screen.getByTestId("sidebar-session-checkbox-s1");
    expect(checkbox).toBeInTheDocument();
    fireEvent.click(checkbox);
    expect(onToggleSelect).toHaveBeenCalledWith("s1");
    expect(onClick).not.toHaveBeenCalled();
  });
});
