import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProgressPanel } from "../src/components/right-panel/ProgressPanel";
import { MessageItem } from "../src/components/chat/MessageItem";
import { VerificationBadge } from "../src/components/chat/VerificationBadge";
import { useChat, useSessionStore, useTaskStore } from "../src/stores";
import { typedIPC } from "../src/ipc";
import type { SendMessageResult, TaskRow, VerificationEvidence } from "../src/types/ipc";

const unknown: VerificationEvidence = { version: 1, verdict: "unknown", reason: "execution_budget_paused", requires_review: true };
const row: TaskRow = { id: "task", session_id: "s", title: "task", status: "budget_paused", progress: 100,
  run_id: "run", execution_status: "budget_paused", verification: unknown, result: "saved partial output",
  error: null, created_at: "2026-01-01", started_at: "2026-01-01", completed_at: "2026-01-01", cancel_supported: false };

beforeEach(() => {
  useChat.getState().reset();
  useTaskStore.getState().clear();
  useSessionStore.setState({ currentSessionId: "s", sessions: [{ id: "s", title: "s", engine: "native", archived: false, created_at: 1, updated_at: 1, model_id: null }] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); useChat.getState().reset(); });

describe("durable execution and verification outcomes", () => {
  it.each([
    ["budget_paused", "预算暂停"], ["interrupted", "执行中断"], ["cancelled", "已取消"], ["requires_review", "需要核对"],
  ] as const)("restores %s and partial output after clearing the UI store", async (status, label) => {
    vi.spyOn(typedIPC, "listTasks").mockResolvedValue({ tasks: [{ ...row, status }] });
    await act(async () => useTaskStore.getState().refresh());
    await act(async () => { render(<ProgressPanel />); });
    expect(screen.getByTestId(`task-status-${status}`)).toHaveTextContent(label);
    expect(screen.getByTestId("task-result-task")).toHaveTextContent("saved partial output");
    expect(screen.getByTestId("verification-verdict")).toHaveTextContent("校验未知");
    expect(screen.queryByText("验收通过")).not.toBeInTheDocument();
  });

  it("keeps execution completion distinct from missing verification", async () => {
    vi.spyOn(typedIPC, "listTasks").mockResolvedValue({ tasks: [{ ...row, status: "completed", execution_status: "completed",
      verification: { version: 1, verdict: "unknown", reason: "not_configured", requires_review: false } }] });
    await act(async () => { render(<ProgressPanel />); });
    await screen.findByText("执行状态：执行结束");
    expect(screen.getByTestId("verification-verdict")).toHaveTextContent("未配置验收命令");
    expect(screen.queryByText("配置的校验已通过")).not.toBeInTheDocument();
  });

  it("never colors a reviewed result as a clean pass and keeps legacy evidence unknown", () => {
    const { rerender } = render(<VerificationBadge evidence={{ version: 1, verdict: "passed", requires_review: true }} />);
    expect(screen.getByTestId("verification-verdict")).toHaveClass("text-status-warning");
    rerender(<VerificationBadge evidence={{ requires_review: false, failed_tool_calls: 0 }} />);
    expect(screen.getByTestId("verification-verdict")).toHaveTextContent("校验未知");
    expect(screen.getByTestId("verification-verdict")).not.toHaveClass("text-status-success");
  });

  it("cancel acknowledgement keeps the task running until durable cleanup", async () => {
    const running: TaskRow = { ...row, status: "running", execution_status: "running", cancel_supported: true };
    vi.spyOn(typedIPC, "listTasks").mockResolvedValue({ tasks: [running] });
    vi.spyOn(typedIPC, "cancelTask").mockResolvedValue({ ok: true, requested: true, task: running });
    await useTaskStore.getState().refresh();
    await useTaskStore.getState().cancel("task");
    expect(useTaskStore.getState().tasks.task.status).toBe("running");
    expect(useTaskStore.getState().tasks.task.cancel_pending).toBe(true);
  });

  it("a delayed cancellation ACK cannot overwrite a terminal event", async () => {
    const running: TaskRow = { ...row, status: "running", execution_status: "running", cancel_supported: true };
    vi.spyOn(typedIPC, "listTasks").mockResolvedValue({ tasks: [running] });
    let acknowledge!: (value: Awaited<ReturnType<typeof typedIPC.cancelTask>>) => void;
    vi.spyOn(typedIPC, "cancelTask").mockImplementation(() => new Promise((resolve) => { acknowledge = resolve; }));
    await useTaskStore.getState().refresh();
    const cancel = useTaskStore.getState().cancel("task");
    useTaskStore.setState((state) => ({ tasks: { ...state.tasks, task: { ...state.tasks.task, status: "cancelled", cancel_pending: false } } }));
    acknowledge({ ok: true, requested: true, task: running });
    await cancel;
    expect(useTaskStore.getState().tasks.task.status).toBe("cancelled");
    expect(useTaskStore.getState().tasks.task.cancel_pending).toBe(false);
  });

  it("loading validation history preserves its active representation", async () => {
    vi.spyOn(typedIPC, "listMessages").mockResolvedValue({ messages: [{ id: "validating", role: "assistant", text: "saved answer",
      streaming: true, status: "streaming", created_at: 1, metadata: { status: "running", run_status: "running", verification: unknown } }] });
    await useChat.getState().loadMessages("s");
    expect(useChat.getState().messages[0].streaming).toBe(true);
    expect(useChat.getState().messages[0].status).toBe("streaming");
  });

  it.each([false, true])("copies send receipt evidence immediately (existing stream=%s)", async (existing) => {
    const evidence: VerificationEvidence = { version: 1, verdict: "failed", reason: "validation_failed", requires_review: true };
    const result: SendMessageResult = { session_id: "s", message_id: "answer", run_id: "run", text: "answer text", cancelled: false,
      run_status: "completed", verification: evidence };
    vi.spyOn(typedIPC, "sendMessage").mockImplementation(async () => {
      if (existing) useChat.setState({ messages: [{ id: "answer", role: "assistant", text: "stream text", streaming: false, created_at: 1 }] });
      return result;
    });
    await act(async () => useChat.getState().send("work"));
    const answer = useChat.getState().messages.find((m) => m.id === "answer")!;
    expect(answer.metadata?.verification).toEqual(evidence);
    expect(answer.metadata?.status).toBe("completed");
    render(<MessageItem message={answer} />);
    expect(screen.getByTestId("verification-verdict")).toHaveTextContent("校验未通过");
  });

  it("copies continuation receipt evidence without waiting for history", async () => {
    vi.spyOn(typedIPC, "continueRun").mockResolvedValue({ session_id: "s", message_id: "continued", run_id: "child", text: "continued output",
      cancelled: false, run_status: "completed", verification: unknown });
    await act(async () => useChat.getState().continueRun("source"));
    await waitFor(() => expect(useChat.getState().messages.find((m) => m.id === "continued")?.metadata?.verification).toEqual(unknown));
  });

  it("shows Native interrupted history text and its real status", () => {
    render(<MessageItem message={{ id: "partial", role: "assistant", text: "Native partial", streaming: false,
      status: "interrupted", created_at: 1, metadata: { status: "interrupted", partial: true, verification: unknown } }} />);
    expect(screen.getByText("Native partial")).toBeVisible();
    expect(screen.getByTestId("message-status-partial")).toHaveTextContent("执行中断");
    expect(screen.getByTestId("verification-verdict")).toHaveTextContent("校验未知");
  });
});
