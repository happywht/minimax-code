import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { typedIPC } from "../../../ipc";
import { usePermissionStore, useSessionStore } from "../../../stores";
import { Sidebar } from "../Sidebar";

afterEach(() => { cleanup(); usePermissionStore.setState({ pending: {} }); vi.restoreAllMocks(); });

it("shows recent sessions plus the selected older one, and expands without deleting history", async () => {
  const sessions = Array.from({ length: 8 }, (_, index) => ({ id: `s${index}`, title: `任务 ${index}`, project_id: "inbox",
    archived: false, created_at: 8 - index, updated_at: 8 - index, model_id: null }));
  useSessionStore.setState({ sessions, projects: [], currentProjectId: "inbox", currentSessionId: "s7", expandedProjectIds: ["inbox"], loading: false });
  usePermissionStore.setState({ pending: { request: { session_id: "s6" } } as never });
  vi.spyOn(typedIPC, "sessionStats").mockResolvedValue({ total_sessions: 8, total_messages: 0 } as never);
  render(<Sidebar />);
  await screen.findByTestId("sidebar-stats-sessions");
  expect(screen.getByTestId("sidebar-session-s7")).toBeInTheDocument();
  expect(screen.getByTestId("sidebar-session-s6")).toBeInTheDocument();
  expect(screen.queryByTestId("sidebar-session-s5")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "展开另外 1 个任务" }));
  expect(screen.getByTestId("sidebar-session-s5")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "收起更多任务" }));
  expect(screen.queryByTestId("sidebar-session-s5")).not.toBeInTheDocument();
  expect(useSessionStore.getState().sessions).toHaveLength(8);
});
