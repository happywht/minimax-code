import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useRunTimelineStore, useSessionStore } from "../../stores";
import { QueuedTaskInput } from "./QueuedTaskInput";

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1", sessions: [] });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("retains an uncertain request and retries with the same key", async () => {
  const call = vi.spyOn(typedIPC, "startRun");
  call.mockRejectedValueOnce(new Error("offline"));
  call.mockImplementationOnce(async (request) => ({ created: false, run: {
    id: "r1", session_id: request.session_id, mode: "execute", status: "queued", title: "task",
    request_key: request.idempotency_key, created_at: "now",
  } }));
  render(<QueuedTaskInput sessionId="s1" />);
  fireEvent.click(screen.getByText("排队一个新任务"));
  fireEvent.change(screen.getByLabelText("排队任务内容"), { target: { value: "next instruction" } });
  fireEvent.click(screen.getByText("提交排队任务"));
  await screen.findByText(/尚未确认是否接收/);
  expect(screen.getByLabelText("排队任务内容")).toHaveValue("next instruction");
  fireEvent.click(screen.getByText("重试确认同一任务"));
  await screen.findByText(/任务已接收/);
  expect(call.mock.calls[1][0]).toEqual(call.mock.calls[0][0]);
  expect(screen.getByLabelText("排队任务内容")).toHaveValue("");
});

it("does not clear a request when the user changes session during admission", async () => {
  let resolve!: (value: Awaited<ReturnType<typeof typedIPC.startRun>>) => void;
  const call = vi.spyOn(typedIPC, "startRun").mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = render(<QueuedTaskInput sessionId="s1" />);
  fireEvent.click(screen.getByText("排队一个新任务"));
  fireEvent.change(screen.getByLabelText("排队任务内容"), { target: { value: "keep" } });
  fireEvent.click(screen.getByText("提交排队任务"));
  const request = call.mock.calls[0][0];
  useSessionStore.setState({ currentSessionId: "s2" });
  view.rerender(<QueuedTaskInput sessionId="s2" />);
  resolve({ created: true, run: { id: "r", session_id: "s1", mode: "execute", status: "queued", title: "t",
    request_key: request.idempotency_key, created_at: "now" } });
  await waitFor(() => expect(localStorage.length).toBeGreaterThan(0));
  expect(screen.queryByText(/任务已接收/)).toBeNull();
});
