import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import * as confirmation from "../modals/ConfirmationDialog";
import { OutputStorage } from "./OutputStorage";

const item = { output_ref: "output_1", run_id: "run1", session_id: "s1", tool_name: "exec_command",
  bytes: 100, sha256: "a".repeat(64), created_at: "2026-10-29", run_status: "completed", cleared_at: null };
const inventory = { session_id: "s1", project_id: "p1", service_id: "svc", total_bytes: 100,
  session_bytes: 100, limit_bytes: 33554432, offset: 0, has_more: false, items: [item] };
beforeEach(() => useSessionStore.setState({ currentSessionId: "s1", sessions: [{ id: "s1", title: "one",
  project_id: "p1", engine: "native", archived: false, created_at: 0, updated_at: 0, model_id: null }] }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows usage and clears only the chosen reviewed output", async () => {
  vi.spyOn(confirmation, "requestConfirmation").mockResolvedValue(true);
  const call = vi.spyOn(ipc, "request").mockResolvedValueOnce(inventory)
    .mockResolvedValueOnce({ output_ref: "output_1", run_id: "run1", session_id: "s1", service_id: "svc" })
    .mockResolvedValueOnce({ ...inventory, total_bytes: 0, session_bytes: 0, items: [{ ...item, cleared_at: "now" }] });
  render(<OutputStorage />);
  fireEvent.click(screen.getByRole("button", { name: "工具输出存储与清理" }));
  expect(await screen.findByText(/全库：/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "清理此捕获输出" }));
  await waitFor(() => expect(call).toHaveBeenCalledWith("run.output_clear", { run_id: "run1", session_id: "s1",
    output_ref: "output_1", expected_service_id: "svc", expected_sha256: "a".repeat(64) }));
  expect(await screen.findByText("已清理，无法再读取")).toBeVisible();
  expect(screen.getByRole("button", { name: "清理此捕获输出" })).toBeDisabled();
});

it("does not clear after the user changes session during confirmation", async () => {
  let resolve!: (accepted: boolean) => void;
  vi.spyOn(confirmation, "requestConfirmation").mockReturnValue(new Promise((done) => { resolve = done; }));
  const call = vi.spyOn(ipc, "request").mockResolvedValue(inventory);
  render(<OutputStorage />);
  fireEvent.click(screen.getByRole("button", { name: "工具输出存储与清理" }));
  await screen.findByText(/全库：/);
  fireEvent.click(screen.getByRole("button", { name: "清理此捕获输出" }));
  act(() => useSessionStore.setState({ currentSessionId: "s2" }));
  await act(async () => resolve(true));
  expect(call).not.toHaveBeenCalledWith("run.output_clear", expect.anything());
});
