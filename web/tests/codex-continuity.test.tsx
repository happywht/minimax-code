import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "../src/components/chat/ChatPanel";
import { MessageInput } from "../src/components/chat/MessageInput";
import { MessageItem } from "../src/components/chat/MessageItem";
import { useChat, useSessionStore } from "../src/stores";
import { useRunTimelineStore } from "../src/stores/runStore";
import { typedIPC } from "../src/ipc";
import type { AgentRun, Session } from "../src/types/ipc";

const session: Session = { id: "codex", title: "Codex task", engine: "codex", archived: false,
  created_at: 1, updated_at: 1, model_id: null };
const run: AgentRun = { id: "run-codex", session_id: "codex", mode: "chat", title: "task",
  status: "queued", created_at: "t", user_message_id: "saved-user", assistant_message_id: "saved-answer",
  metadata: { engine: "codex", workspace_snapshot: "/project", codex_thread_id: "thread-1",
    codex_protocol: "codex-cli-0.163.0" } };

beforeEach(() => {
  useChat.getState().reset();
  useSessionStore.setState({ currentSessionId: session.id, sessions: [session], creating: false });
  useRunTimelineStore.setState({ runs: {}, order: [], loading: false });
  vi.spyOn(typedIPC, "listEngines").mockResolvedValue({ default: "native", engines: [
    { id: "native", name: "Native", available: true, unavailable_reason: null, version: "test", capabilities: {} },
    { id: "codex", name: "Codex", available: true, unavailable_reason: null, version: "0.163.0", capabilities: { resume: true, steer: false } },
  ] });
});
afterEach(() => { cleanup(); useChat.getState().reset(); vi.restoreAllMocks(); });

describe("Codex conversation UI", () => {
  it("creates the selected engine then sends through run.start and restores durable history", async () => {
    useSessionStore.setState({ currentSessionId: "native", sessions: [{ ...session, id: "native", engine: "native" }] });
    const create = vi.spyOn(typedIPC, "createSession").mockResolvedValue({ session_id: session.id, session });
    const start = vi.spyOn(typedIPC, "startRun").mockResolvedValue({ run, created: true });
    const send = vi.spyOn(typedIPC, "sendMessage");
    const history = vi.spyOn(typedIPC, "listMessages").mockResolvedValue({ messages: [
      { id: "saved-user", role: "user", text: "hello", created_at: 1, metadata: { run_id: run.id, engine: "codex" } },
      { id: "saved-answer", role: "assistant", text: "persisted answer", created_at: 2,
        metadata: { run_id: run.id, engine: "codex", tokens_in: null, tokens_out: null, usage_certainty: "unknown" } },
    ] });
    const refresh = vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockImplementation(async () => {
      await useChat.getState().reconcileRuns([{ ...run, status: "completed" }], () => true);
    });
    render(<><ChatPanel /><MessageInput loadAgents={async () => []} /></>);
    await screen.findByRole("option", { name: "Codex" });
    fireEvent.change(screen.getByRole("combobox", { name: "新会话引擎" }), { target: { value: "codex" } });
    fireEvent.click(screen.getByTestId("chat-header-new"));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ engine: "codex" })));
    await waitFor(() => expect(useSessionStore.getState().currentSessionId).toBe("codex"));
    fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "hello" } });
    fireEvent.submit(screen.getByTestId("message-input"));
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ session_id: "codex", engine: "codex", content: "hello" })));
    await waitFor(() => expect(history).toHaveBeenCalled());
    expect(send).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledWith("codex");
    expect(useChat.getState().messages.map((m) => m.id)).toEqual(["saved-user", "saved-answer"]);
    expect(useChat.getState().messages[0].render_key).toMatch(/^user-/);
    expect(useChat.getState().messages[1].metadata?.tokens_in).toBeNull();
    expect(screen.getByTestId("message-attach-btn")).toBeDisabled();
    expect(screen.getByTestId("message-image-btn")).toBeDisabled();
    expect(screen.getByTestId("chat-input-always-allow")).toBeDisabled();
    expect(screen.getByTestId("codex-model-config")).toHaveTextContent("Codex CLI 配置");
    expect(screen.queryByTestId("context-indicator")).not.toBeInTheDocument();
    expect(screen.queryByTestId("model-selector-inline")).not.toBeInTheDocument();
  });

  it("shows Codex recovery without a Native model snapshot and submits the reviewed budget", async () => {
    const paused: AgentRun = { ...run, status: "budget_paused", started_at: "t" };
    useRunTimelineStore.setState({ runs: { [run.id]: { ...paused, steps: [] } }, order: [run.id] });
    const resume = vi.spyOn(typedIPC, "resumeRun").mockResolvedValue({ run, created: true });
    vi.spyOn(useRunTimelineStore.getState(), "loadForSession").mockResolvedValue(undefined);
    render(<ChatPanel />);
    expect(screen.getByTestId("run-recovery-actions")).toBeVisible();
    expect(screen.getByRole("button", { name: "继续此任务" })).toBeDisabled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "恢复 token 上限" }), { target: { value: "10000" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "继续此任务" }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith(run.id, expect.any(String), true, 10000));
  });

  it("rejects unsupported context before clearing the draft or admitting work", async () => {
    const start = vi.spyOn(typedIPC, "startRun");
    render(<MessageInput loadAgents={async () => []} />);
    fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "@repo inspect" } });
    await act(async () => fireEvent.submit(screen.getByTestId("message-input")));
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByTestId("message-input-textarea")).toHaveValue("@repo inspect");
    expect(useChat.getState().error).toContain("纯文本");
  });

  it("keeps partial Codex text visible and explains unknown usage after history restore", async () => {
    await act(async () => { await import("../src/components/chat/MessageItem"); });
    render(<MessageItem message={{ id: "partial", role: "assistant", text: "saved partial", status: "failed",
      streaming: false, created_at: 1, metadata: { engine: "codex", partial: true, usage_certainty: "unknown",
        tokens_in: null, tokens_out: null } }} />);
    expect(await screen.findByText("saved partial")).toBeVisible();
    expect(screen.getByText("用量未知或不确定")).toBeVisible();
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  });
});
