import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { WorkspaceDiagnostic } from "./WorkspaceDiagnostic";

beforeEach(() => useSessionStore.setState({ currentSessionId: "s1", sessions: ["s1", "s2"].map((id) => ({
  id, title: id, project_id: "p1", engine: "native", archived: false, created_at: 0, updated_at: 0, model_id: null })) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows a failed independent workspace with actionable guidance", async () => {
  vi.spyOn(ipc, "request").mockResolvedValue({ session_id: "s1", project_id: "p1", service_id: "svc",
    status: "error", source: "session", reason: "directory_unavailable", path: null });
  render(<WorkspaceDiagnostic />);
  fireEvent.click(screen.getByRole("button", { name: "检查当前会话目录" }));
  expect(await screen.findByText(/目录不可用 · 会话独立目录/)).toBeVisible();
  expect(screen.getByText(/修改项目根目录不会改变此会话绑定/)).toBeVisible();
  expect(screen.getByText(/未测试写入权限、模型连接或远程执行/)).toBeVisible();
});

it("does not display a delayed result after switching sessions", async () => {
  let resolve!: (value: unknown) => void;
  vi.spyOn(ipc, "request").mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<WorkspaceDiagnostic />);
  fireEvent.click(screen.getByRole("button", { name: "检查当前会话目录" }));
  act(() => useSessionStore.setState({ currentSessionId: "s2" }));
  await act(async () => { resolve({ session_id: "s1", project_id: "p1", service_id: "svc",
    status: "ok", source: "session", path: "private-old", reason: null }); });
  expect(screen.queryByText(/private-old/)).toBeNull();
});
