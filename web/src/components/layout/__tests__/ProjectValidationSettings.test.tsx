import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { typedIPC } from "../../../ipc";
import { useSessionStore } from "../../../stores";
import { Sidebar } from "../Sidebar";

const project = { id: "p1", name: "Project", description: "", root_path: "/project", validate_command: "old test",
  archived: false, created_at: 1, updated_at: 1 };

beforeEach(() => {
  useSessionStore.setState({ projects: [project], currentProjectId: "p1", sessions: [{
    id: "s1", title: "Task", project_id: "p1", archived: false, created_at: 1, updated_at: 1, model_id: null,
  }] });
  vi.spyOn(typedIPC, "sessionStats").mockResolvedValue({ total_sessions: 1, total_messages: 0 } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("project validation settings", () => {
  it("updates an existing command through project.update and reflects the saved value", async () => {
    const update = vi.spyOn(typedIPC, "updateProject").mockResolvedValue({ ok: true,
      project: { ...project, validate_command: "pytest -q" } } as never);
    render(<Sidebar />);
    fireEvent.click(screen.getByTestId("sidebar-project-menu-p1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "项目验证设置" }));
    expect(screen.getByLabelText("任务验证命令")).toHaveValue("old test");
    fireEvent.change(screen.getByLabelText("任务验证命令"), { target: { value: "pytest -q" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("p1", { validate_command: "pytest -q" }));
    await waitFor(() => expect(screen.queryByTestId("sidebar-project-validation-settings")).toBeNull());
    expect(useSessionStore.getState().projects.find((row) => row.id === "p1")?.validate_command).toBe("pytest -q");
  });

  it("retains the user's draft when saving fails", async () => {
    vi.spyOn(typedIPC, "updateProject").mockRejectedValue(new Error("offline"));
    render(<Sidebar />);
    fireEvent.click(screen.getByTestId("sidebar-project-menu-p1"));
    fireEvent.click(screen.getByRole("menuitem", { name: "项目验证设置" }));
    fireEvent.change(screen.getByLabelText("任务验证命令"), { target: { value: "new test" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存设置" })).not.toBeDisabled());
    expect(screen.getByLabelText("任务验证命令")).toHaveValue("new test");
    expect(useSessionStore.getState().projects[0].validate_command).toBe("old test");
  });
});
