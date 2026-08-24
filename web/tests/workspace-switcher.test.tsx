/**
 * Tests for the WorkspaceSwitcher dropdown (v1.2.2: project switcher).
 *
 * The component is wired to the real project system — the same
 * `sessionStore` slice the sidebar selector drives — instead of the
 * removed localStorage-only workspace list (`lib/workspace.ts`).
 *
 * Covers:
 *   1. Renders the current project name (falls back to the inbox
 *      seed when no project is selected).
 *   2. Clicking the trigger opens the dropdown and lists every
 *      project (inbox first) with a checkmark on the active one.
 *   3. Selecting another project persists `currentProjectId` in the
 *      store (and localStorage) *without* wiping the session list —
 *      projects are grouping labels, switching only routes new
 *      sessions.
 *   4. The inline create row creates a project and selects it.
 */
import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WorkspaceSwitcher } from "../src/components/layout/WorkspaceSwitcher";
import { useSessionStore } from "../src/stores";
import type { Project } from "../src/types/ipc";

vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

function makeProject(overrides: Partial<Project> & { id: string }): Project {
  return {
    name: overrides.id,
    description: "",
    root_path: "",
    archived: false,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  } as Project;
}

function seedProjects(projects: Project[], currentProjectId: string | null) {
  useSessionStore.setState({ projects, currentProjectId });
}

const SAMPLE_SESSION = {
  id: "ses_keep",
  title: "existing task",
  archived: false,
  created_at: 0,
  updated_at: 0,
  model_id: null,
};

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    sessions: [SAMPLE_SESSION] as never,
    projects: [],
    currentProjectId: null,
    currentSessionId: "ses_keep",
    loading: false,
    loadingProjects: false,
    filter: "all",
  });
});

afterEach(() => {
  localStorage.clear();
});

describe("WorkspaceSwitcher (project switcher)", () => {
  it("falls back to the inbox project when nothing is selected", () => {
    seedProjects(
      [makeProject({ id: "inbox", name: "收件箱" }), makeProject({ id: "p1", name: "官网改版" })],
      null,
    );
    render(<WorkspaceSwitcher />);
    expect(screen.getByTestId("workspace-switcher")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent("收件箱");
  });

  it("renders the current project name", () => {
    seedProjects(
      [makeProject({ id: "inbox", name: "收件箱" }), makeProject({ id: "p1", name: "官网改版" })],
      "p1",
    );
    render(<WorkspaceSwitcher />);
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent("官网改版");
  });

  it("synthesizes an inbox entry when the store has no projects yet", () => {
    seedProjects([], null);
    render(<WorkspaceSwitcher />);
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent("收件箱");
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByTestId("workspace-option-inbox")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("lists inbox first with a checkmark on the active project", () => {
    seedProjects(
      [
        makeProject({ id: "p1", name: "官网改版", updated_at: 5 }),
        makeProject({ id: "inbox", name: "收件箱" }),
        makeProject({ id: "p2", name: "移动端", updated_at: 9 }),
      ],
      "p2",
    );
    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    const menu = screen.getByTestId("workspace-switcher-menu");
    expect(menu).toBeInTheDocument();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    // Inbox is always first; the active project carries the checkmark.
    expect(screen.getByTestId("workspace-option-inbox")).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByTestId("workspace-option-p2")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("workspace-switcher-check")).toBeInTheDocument();
  });

  it("switching a project updates the store + localStorage and keeps the session list", async () => {
    seedProjects(
      [makeProject({ id: "inbox", name: "收件箱" }), makeProject({ id: "p1", name: "官网改版" })],
      null,
    );
    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    fireEvent.click(screen.getByTestId("workspace-option-p1"));

    // Selection persisted in the store and to localStorage.
    expect(useSessionStore.getState().currentProjectId).toBe("p1");
    await waitFor(() => {
      expect(localStorage.getItem("minimax-code:current-project")).toBe("p1");
    });
    // Label reflects the new project.
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent("官网改版");
    // v1.2.2 semantics: projects are grouping labels — the existing
    // session list is NOT cleared on switch.
    expect(useSessionStore.getState().sessions).toHaveLength(1);
    // Dropdown closed after the selection.
    expect(screen.queryByTestId("workspace-switcher-menu")).toBeNull();
  });

  it("creating a project from the inline row selects it and appends it", async () => {
    const createdProject = makeProject({ id: "p_new", name: "新项目" });
    // Mirror the real store action: it appends the project to the
    // list before returning it (the component only calls setCurrentProject).
    const createProject = vi.fn(async () => {
      useSessionStore.setState((s) => ({
        projects: [createdProject, ...s.projects],
      }));
      return createdProject;
    });
    seedProjects([makeProject({ id: "inbox", name: "收件箱" })], null);
    useSessionStore.setState({ createProject });

    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    const input = screen.getByTestId("workspace-switcher-create-input");
    fireEvent.change(input, { target: { value: "新项目" } });
    fireEvent.click(screen.getByTestId("workspace-switcher-create-submit"));

    await waitFor(() => {
      expect(useSessionStore.getState().currentProjectId).toBe("p_new");
    });
    expect(createProject).toHaveBeenCalledWith("新项目");
    // Dropdown closed and the label shows the new project.
    await waitFor(() => {
      expect(screen.queryByTestId("workspace-switcher-menu")).toBeNull();
    });
    expect(screen.getByTestId("workspace-switcher-label")).toHaveTextContent("新项目");
  });

  it("empty name does not call createProject", async () => {
    const createProject = vi.fn(async () => null);
    seedProjects([makeProject({ id: "inbox", name: "收件箱" })], null);
    useSessionStore.setState({ createProject });

    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));
    fireEvent.change(screen.getByTestId("workspace-switcher-create-input"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("workspace-switcher-create-submit"));

    expect(createProject).not.toHaveBeenCalled();
    // The menu stays open so the user can fix the name.
    expect(screen.getByTestId("workspace-switcher-menu")).toBeInTheDocument();
  });

  it("surfaces a rooted project's root_path as the option tooltip (v1.3.0)", () => {
    seedProjects(
      [
        makeProject({ id: "inbox", name: "收件箱" }),
        makeProject({ id: "pA", name: "项目A", root_path: "D:\\tmp\\projA" }),
        makeProject({ id: "pB", name: "项目B" }), // unrooted
      ],
      "pA",
    );

    render(<WorkspaceSwitcher />);
    fireEvent.click(screen.getByTestId("workspace-switcher-trigger"));

    expect(screen.getByTestId("workspace-option-pA")).toHaveAttribute("title", "D:\\tmp\\projA");
    // Unrooted projects render no tooltip instead of an empty title.
    expect(screen.getByTestId("workspace-option-pB")).not.toHaveAttribute("title");
    expect(screen.getByTestId("workspace-option-inbox")).not.toHaveAttribute("title");
  });
});
