import { waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ipc } from "../../ipc";
import { useSessionStore } from "../sessionStore";
import { useSubAgentStore as store } from "../subAgent";

const child = { run_id: "child", session_id: "child-session", parent_run_id: "parent", status: "running",
  live: false, agent_id: "worker", agent_name: "Worker", prompt: "work", text: "", partial: false,
  error: null, text_truncated: false, created_at: "2026-10-29T00:00:00Z", completed_at: null };
const result = { session_id: "one", project_id: "p1", service_id: "service", limit: 100, children: [child] };
beforeEach(() => {
  store.getState().reset();
  useSessionStore.setState({ currentSessionId: "one", sessions: ["one", "two"].map((id) => ({ id, title: id,
    project_id: "p1", engine: "native", archived: false, created_at: 0, updated_at: 0, model_id: null })) });
});

it("recovers durable children without reviving unconfirmed runs or applying stale events", async () => {
  const call = vi.spyOn(ipc, "request").mockResolvedValue(result);
  await store.getState().recoverChildren("one");
  expect(store.getState().runs.child.status).toBe("unknown");
  expect(store.getState().runs.child.parent_run_id).toBe("parent");
  store.getState().applyProgress({ run_id: "child", agent_id: "worker", parent_session_id: "one",
    status: "thinking", progress: 0.5, summary: "late", received_at: Date.now() });
  expect(store.getState().runs.child.status).toBe("unknown");
  call.mockResolvedValue({ ...result, children: [{ ...child, status: "completed", text: "done" }] });
  await store.getState().recoverChildren("one");
  expect(Object.keys(store.getState().runs)).toEqual(["child"]);
  expect(store.getState().runs.child.status).toBe("completed");
  call.mockRestore();
});

it("discards a delayed response after the session changes", async () => {
  let resolve!: (value: typeof result) => void;
  const call = vi.spyOn(ipc, "request").mockReturnValue(new Promise((done) => { resolve = done; }));
  const pending = store.getState().recoverChildren("one");
  useSessionStore.setState({ currentSessionId: "two" });
  resolve(result); await pending;
  expect(store.getState().runs.child).toBeUndefined();
  call.mockRestore();
});

it("marks active work uncertain on disconnect and recovers it on reconnect", async () => {
  let sidecar!: (event: { status: "started" | "stopped" }) => void;
  const start = vi.spyOn(ipc, "start").mockResolvedValue();
  const listen = vi.spyOn(ipc, "on").mockReturnValue(() => {});
  const subscribe = vi.spyOn(ipc, "onSideCar").mockImplementation((callback) => { sidecar = callback; return () => {}; });
  const call = vi.spyOn(ipc, "request").mockResolvedValue({ ...result, children: [{ ...child, live: true }] });
  await store.getState().init();
  await store.getState().recoverChildren("one");
  expect(store.getState().runs.child.status).toBe("thinking");
  sidecar({ status: "stopped" });
  expect(store.getState().runs.child.status).toBe("unknown");
  call.mockResolvedValue({ ...result, children: [{ ...child, status: "completed" }] });
  sidecar({ status: "started" });
  await waitFor(() => expect(store.getState().runs.child?.status).toBe("completed"));
  start.mockRestore(); listen.mockRestore(); subscribe.mockRestore(); call.mockRestore();
});
