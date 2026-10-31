import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useRunTimelineStore, useSessionStore } from "../../stores";
import { useQueuedTask } from "./useQueuedTask";

const input = { content: "next", draft: "next", mode: "plan" as const, budgetTokens: 500,
  restrictNetworkTools: true, captureFileChanges: true };
beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1",
    sessions: [{ id: "s1", project_id: "p1", engine: "native" } as never] });
  useChat.setState({ status: "streaming" });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(typedIPC, "getServiceIdentity").mockResolvedValue({ service_id: "service-1" });
  vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("retains the exact bound snapshot after a lost response and retries idempotently", async () => {
  const start = vi.spyOn(typedIPC, "startRun");
  start.mockRejectedValueOnce(new Error("offline"));
  start.mockImplementationOnce(async (request) => ({ created: false, run: {
    id: "r", session_id: request.session_id, request_key: request.idempotency_key,
    mode: "plan", status: "queued", title: "t", created_at: "now", metadata: { project_id: "p1" },
  } }));
  const { result } = renderHook(() => useQueuedTask("s1"));
  let outcome: string | undefined;
  await act(async () => { outcome = await result.current.submit(input); });
  expect(outcome).toBe("uncertain");
  expect(result.current.pending?.request).toMatchObject({ expected_service_id: "service-1", expected_project_id: "p1",
    mode: "plan", budget_tokens: 500, restrict_network_tools: true, capture_file_changes: true });
  await act(async () => { outcome = await result.current.retry(); });
  expect(outcome).toBe("accepted");
  expect(start.mock.calls[1][0]).toEqual(start.mock.calls[0][0]);
  expect(result.current.pending).toBeNull();
});

it("fails closed for corrupt pending state and during session cancellation", async () => {
  const key = `minimax-code:queued-input:${encodeURIComponent(ipc.agentBaseUrl)}:s1:pending`;
  localStorage.setItem(key, "invalid");
  const start = vi.spyOn(typedIPC, "startRun");
  const { result } = renderHook(() => useQueuedTask("s1"));
  expect(result.current.blocked).toBe(true);
  await act(async () => { expect(await result.current.submit(input)).toBe("rejected"); });
  expect(start).not.toHaveBeenCalled();
  act(() => result.current.forget());
  useChat.setState({ status: "cancelling" });
  await act(async () => { expect(await result.current.submit(input)).toBe("rejected"); });
  expect(start).not.toHaveBeenCalled();
});


it("rejects a project switch during identity lookup without sending", async () => {
  let resolve!: (v: { service_id: string }) => void;
  vi.mocked(typedIPC.getServiceIdentity).mockReturnValue(new Promise((done) => { resolve = done; }));
  const start = vi.spyOn(typedIPC, "startRun");
  const { result } = renderHook(() => useQueuedTask("s1"));
  let outcome: string | undefined;
  await act(async () => {
    const task = result.current.submit(input);
    useSessionStore.setState({ currentProjectId: "other" });
    resolve({ service_id: "service-1" });
    outcome = await task;
  });
  expect(outcome).toBe("rejected");
  expect(start).not.toHaveBeenCalled();
});

it("persists image content before dispatch", async () => {
  const start = vi.spyOn(typedIPC, "startRun").mockRejectedValueOnce(new Error("offline"));
  const { result } = renderHook(() => useQueuedTask("s1"));
  const content = [{ type: "text" as const, text: "inspect" },
    { type: "image" as const, media_type: "image/png", data: "ZmFrZQ==" }];
  await act(async () => { expect(await result.current.submit({ ...input, content })).toBe("uncertain"); });
  expect(start.mock.calls[0][0].content).toEqual(content);
  expect(result.current.pending?.request.content).toEqual(content);
});
