import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useRunTimelineStore, useSessionStore } from "../../stores";
import type { AgentRun } from "../../types/ipc";
import { NativeSteerInput } from "./NativeSteerInput";

const run = { id: "r1", session_id: "s1", mode: "execute", status: "running", title: "task",
  created_at: "now", metadata: { model_snapshot: { engine: "native" } } } as AgentRun;
beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ currentSessionId: "s1" });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("retries the same supplemental input after an uncertain response", async () => {
  const call = vi.spyOn(typedIPC, "steerRun").mockRejectedValueOnce(new Error("offline"));
  call.mockImplementationOnce(async (request) => ({ step: { id: "step", run_id: request.run_id,
    session_id: request.session_id, kind: "observation", status: "pending", title: "补充", summary: "等待",
    started_at: "now", ordinal: 1, payload: { source: "native_steer", text: request.text } } }));
  render(<NativeSteerInput run={run} />);
  fireEvent.click(screen.getByText("向此 Native 任务补充"));
  fireEvent.change(screen.getByLabelText("运行中补充内容"), { target: { value: "extra" } });
  fireEvent.click(screen.getByText("发送补充"));
  await screen.findByText(/无法确认是否接收/);
  fireEvent.click(screen.getByText("重试确认同一补充"));
  await screen.findByText(/已确认记录/);
  expect(call.mock.calls[1][0]).toEqual(call.mock.calls[0][0]);
});

it("offers a Codex-specific control", () => {
  render(<NativeSteerInput run={{ ...run, metadata: { engine: "codex" } }} />);
  expect(screen.getByText("向此 Codex 任务补充")).toBeTruthy();
});

it("restores an unsent draft after the timeline is reopened and keeps it after the run ends", () => {
  const view = render(<NativeSteerInput run={run} />);
  fireEvent.click(screen.getByText("向此 Native 任务补充"));
  fireEvent.change(screen.getByLabelText("运行中补充内容"), { target: { value: "unsent details" } });
  view.unmount();
  const reopened = render(<NativeSteerInput run={{ ...run, status: "completed" }} />);
  expect((screen.getByLabelText("运行中补充内容") as HTMLTextAreaElement).value).toBe("unsent details");
  expect(screen.getByText(/任务已结束，草稿未发送/)).toBeTruthy();
  reopened.unmount();
  render(<NativeSteerInput run={{ ...run, id: "r2" }} />);
  fireEvent.click(screen.getByText("向此 Native 任务补充"));
  expect((screen.getByLabelText("运行中补充内容") as HTMLTextAreaElement).value).toBe("");
});
