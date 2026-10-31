import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentRun, RunStepsResult } from "../../../types/ipc";
import { ipc, typedIPC } from "../../../ipc";
import { useSessionStore } from "../../../stores";

const saved = vi.hoisted(() => ({ text: "" }));
vi.mock("../RunAttachments", () => ({ RunAttachments: () => null }));
vi.mock("../RunTimelineTextArtifacts", () => ({ RunTimelineTextArtifacts: () => null }));
vi.mock("../../chat/VerificationBadge", () => ({ VerificationBadge: () => null }));
vi.mock("../../chat/VerificationDetails", () => ({ VerificationDetails: () => null }));
vi.mock("../RunRecoveryActions", () => ({
  recoveryDraftKey: () => "key", readRetainedRecovery: () => undefined, discardRetainedRecovery: () => {},
  RunRecoveryActions: ({ onEditingChange }: { onEditingChange: (value: boolean) => void }) =>
    <input aria-label="交接草稿" defaultValue={saved.text} onChange={(event) => { saved.text = event.target.value; onEditingChange(!!event.target.value); }} />,
}));
import { ReferenceTargetTimeline } from "../RunTimelinePanel";

const run: AgentRun = { id: "run1", session_id: "session1", mode: "chat", status: "running",
  title: "Original", created_at: "2026-01-01T00:00:00Z", metadata: { project_id: "project1" } };
const target = { version: 1 as const, service_id: "service1", project_id: "project1", session_id: "session1", run_id: "run1", host: ipc.agentBaseUrl };
const snapshot = (overrides: Partial<AgentRun> = {}): RunStepsResult => ({ run: { ...run, ...overrides }, cursor: 1, steps: [] });
const listeners = new Map<string, (event: any) => void>();
const sidecars = new Set<(event: { status: "started" | "stopped" | "error" }) => void>();
const sidecar = (event: { status: "started" | "stopped" | "error" }) => sidecars.forEach((callback) => callback(event));

beforeEach(() => {
  saved.text = "";
  vi.spyOn(ipc, "on").mockImplementation((event, callback) => { listeners.set(event, callback as (event: any) => void); return () => listeners.delete(event); });
  vi.spyOn(ipc, "onSideCar").mockImplementation((callback) => { sidecars.add(callback); return () => { sidecars.delete(callback); }; });
  useSessionStore.setState({ currentProjectId: "project1", currentSessionId: "session1" });
  vi.spyOn(typedIPC, "getProjectRunSummary").mockResolvedValue({ service_id: "service1", project_id: "project1", detail: { id: "run1", session_id: "session1" } } as never);
  vi.spyOn(typedIPC, "getSession").mockResolvedValue({ session: { id: "session1", project_id: "project1", archived: false } } as never);
  vi.spyOn(typedIPC, "listProjects").mockResolvedValue({ projects: [{ id: "project1", archived: false }] } as never);
  vi.spyOn(typedIPC, "getRunSteps").mockResolvedValue(snapshot());
});
afterEach(() => { listeners.clear(); sidecars.clear(); vi.restoreAllMocks(); });

it("coalesces relevant events and reloads after reconnect without applying late data", async () => {
  render(<ReferenceTargetTimeline target={target} onClear={() => {}} />);
  await screen.findByText(/Original · 读取时状态 running/);
  const steps = vi.mocked(typedIPC.getRunSteps);
  act(() => listeners.get("run.updated")?.({ data: { run: { id: "other", session_id: "session1" } } }));
  expect(steps).toHaveBeenCalledTimes(1);
  steps.mockResolvedValueOnce(snapshot({ title: "Updated" }));
  act(() => { for (let i = 0; i < 5; i++) listeners.get("run.updated")?.({ data: { run } }); });
  await screen.findByText(/Updated · 读取时状态 running/);
  expect(steps).toHaveBeenCalledTimes(2);
  let resolve!: (value: RunStepsResult) => void;
  steps.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  act(() => { sidecar({ status: "stopped" }); sidecar({ status: "started" }); });
  await waitFor(() => expect(steps).toHaveBeenCalledTimes(3));
  act(() => sidecar({ status: "stopped" }));
  await act(async () => resolve(snapshot({ title: "Stale" })));
  expect(screen.queryByText(/Stale ·/)).toBeNull();
  steps.mockResolvedValueOnce(snapshot({ title: "Restored", status: "completed" }));
  act(() => sidecar({ status: "started" }));
  await screen.findByText(/Restored · 读取时状态 completed/);
  expect(steps).toHaveBeenCalledTimes(4);
});

it("preserves a recovery handoff and suspends automatic refresh while editing", async () => {
  vi.mocked(typedIPC.getRunSteps).mockResolvedValue(snapshot({ status: "failed" }));
  render(<ReferenceTargetTimeline target={target} onClear={() => {}} />);
  const input = await screen.findByLabelText("交接草稿");
  fireEvent.change(input, { target: { value: "不要重复写入" } });
  expect(screen.getByRole("button", { name: "刷新此运行" })).toBeDisabled();
  act(() => listeners.get("run.updated")?.({ data: { run } }));
  expect(screen.getByText(/交接草稿已保留/)).toBeTruthy();
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(typedIPC.getRunSteps).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue("不要重复写入");
  act(() => sidecar({ status: "stopped" }));
  expect(screen.getByRole("button", { name: "保留草稿并重新核对" })).toBeDisabled();
  act(() => sidecar({ status: "started" }));
  expect(screen.getByRole("button", { name: "保留草稿并重新核对" })).toBeEnabled();
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(typedIPC.getRunSteps).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "保留草稿并重新核对" }));
  expect(await screen.findByLabelText("交接草稿")).toHaveValue("不要重复写入");
});

it("refuses a reconnected service with a different identity", async () => {
  render(<ReferenceTargetTimeline target={target} onClear={() => {}} />);
  await screen.findByText(/Original · 读取时状态 running/);
  vi.mocked(typedIPC.getProjectRunSummary).mockResolvedValue({ service_id: "other", project_id: "project1",
    detail: { id: "run1", session_id: "session1" } } as never);
  act(() => { sidecar({ status: "stopped" }); sidecar({ status: "started" }); });
  await screen.findByRole("alert");
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("运行归属已变化"));
  expect(screen.queryByText(/Original · 读取时状态/)).toBeNull();
  expect(typedIPC.getRunSteps).toHaveBeenCalledTimes(1);
});
