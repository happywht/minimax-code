import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useRunTimelineStore, useSessionStore } from "../../stores";
import type { ContentPart, RunStartParams } from "../../types/ipc";

export type QueuedTaskInput = {
  content: string | ContentPart[];
  draft: string;
  budgetTokens?: number;
  mode: "execute" | "plan";
  restrictNetworkTools: boolean;
  captureFileChanges: boolean;
};
export type PendingQueuedTask = { request: RunStartParams; project: string | null; draft: string };
export type QueueResult = "accepted" | "uncertain" | "rejected";

// Multiple mounted composers must not admit two requests for the same draft.
const inFlight = new Set<string>();
const keyFor = (host: string, session: string) =>
  `minimax-code:queued-input:${encodeURIComponent(host)}:${encodeURIComponent(session)}:pending`;

function readPending(key: string, session: string): PendingQueuedTask | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const value = JSON.parse(raw) as PendingQueuedTask;
  if (!value || value.request?.session_id !== session ||
      typeof value.request.idempotency_key !== "string" || !value.request.idempotency_key ||
      typeof value.draft !== "string" || !(value.project === null || typeof value.project === "string")) {
    throw new Error("本地待确认请求格式无效；请先检查原任务，不能覆盖该记录。");
  }
  return value;
}

/** Admission of a separate durable run; never appends instructions to a running agent. */
export function useQueuedTask(sessionId: string | null) {
  const [host, setHost] = useState(ipc.agentBaseUrl);
  const [pending, setPending] = useState<PendingQueuedTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [loadedScope, setLoadedScope] = useState<string | null>(null);
  const scope = sessionId ? keyFor(host, sessionId) : null;
  const ready = sessionId !== null && loadedScope === scope;
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => ipc.onSideCar(() => {
    ++generation.current;
    setBusy(false); setHost(ipc.agentBaseUrl); setLoadedScope(null);
    try { setPending(sessionId ? readPending(keyFor(ipc.agentBaseUrl, sessionId), sessionId) : null); setBlocked(false); }
    catch { setBlocked(true); setPending(null); setNotice("无法读取本地待确认请求；请先检查原任务。"); }
    setLoadedScope(sessionId ? keyFor(ipc.agentBaseUrl, sessionId) : null);
  }), [sessionId]);
  useEffect(() => {
    ++generation.current;
    setPending(null); setNotice(null); setBusy(false); setBlocked(false); setLoadedScope(null);
    if (!sessionId) return;
    try { setPending(readPending(keyFor(host, sessionId), sessionId)); }
    catch { setBlocked(true); setNotice("无法读取本地待确认请求；请先检查原任务，不能安全提交。"); }
    setLoadedScope(keyFor(host, sessionId));
    return () => { ++generation.current; };
  }, [host, sessionId]);

  const execute = async (input?: QueuedTaskInput): Promise<QueueResult> => {
    if (!sessionId || blocked || !ready) return "rejected";
    const key = keyFor(host, sessionId);
    if (inFlight.has(key)) return "rejected";
    inFlight.add(key);
    const ticket = generation.current;
    const viewCurrent = () => ticket === generation.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === sessionId;
    if (viewCurrent()) { setBusy(true); setNotice(null); }
    let sent = false;
    try {
      let stored: PendingQueuedTask | null;
      try { stored = readPending(key, sessionId); }
      catch { if (viewCurrent()) setBlocked(true); throw new Error("本地待确认请求不可读，请先检查原任务。"); }
      if (input && stored) throw new Error("已有待确认请求，请先核对或重试原请求。");
      if (!input && !stored) throw new Error("没有可重试的待确认请求。");
      const state = useSessionStore.getState();
      const project = state.currentProjectId ?? null;
      const session = state.sessions.find((item) => item.id === sessionId);
      if (!viewCurrent() || !session || (session.project_id ?? null) !== project ||
          useChat.getState().status === "cancelling") {
        throw new Error("服务、会话或项目已变化，或正在停止；未提交。");
      }
      let value: PendingQueuedTask;
      if (stored) {
        if (stored.project !== project || !("expected_project_id" in stored.request) || stored.request.expected_project_id !== project || !stored.request.expected_service_id ||
            stored.request.engine !== (session.engine ?? "native")) {
          throw new Error("原请求的项目、服务身份或引擎不匹配；请核对时间线。");
        }
        value = stored;
      } else {
        if (!input || (typeof input.content === "string" ? !input.content.trim() : !input.content.length) ||
            (input.budgetTokens !== undefined && (!Number.isSafeInteger(input.budgetTokens) || input.budgetTokens <= 0)) ||
            (session.engine === "codex" && input.restrictNetworkTools)) {
          throw new Error("任务内容、预算或联网限制无效；未提交。");
        }
        value = { project, draft: input.draft, request: {
          session_id: sessionId, engine: session.engine ?? "native", content: input.content, expected_project_id: project,
          idempotency_key: crypto.randomUUID(), mode: input.mode,
          ...(input.budgetTokens !== undefined ? { budget_tokens: input.budgetTokens } : {}),
          ...(input.restrictNetworkTools ? { restrict_network_tools: true } : {}),
          ...(input.captureFileChanges ? { capture_file_changes: true } : {}),
        } };
      }
      const identity = await typedIPC.getServiceIdentity();
      const currentState = useSessionStore.getState();
      const currentSession = currentState.sessions.find((item) => item.id === sessionId);
      if (!viewCurrent() || (currentState.currentProjectId ?? null) !== project ||
          (currentSession?.project_id ?? null) !== project || currentSession?.engine !== session.engine ||
          useChat.getState().status === "cancelling") {
        throw new Error("准备期间服务、会话或项目已变化，或正在停止；未提交。");
      }
      if (!identity.service_id || (stored && identity.service_id !== stored.request.expected_service_id)) {
        throw new Error("服务身份不可用或已变化；请核对原任务，不能重试。");
      }
      if (!stored) value = { ...value, request: { ...value.request, expected_service_id: identity.service_id } };
      // Fail closed if storage fails: the response may be lost after admission.
      try { localStorage.setItem(key, JSON.stringify(value)); }
      catch { if (viewCurrent()) setBlocked(true); throw new Error("浏览器存储不可用，无法安全提交。"); }
      if (viewCurrent()) setPending(value);
      sent = true;
      const result = await typedIPC.startRun(value.request);
      if (result.run.session_id !== sessionId || result.run.request_key !== value.request.idempotency_key ||
          (!result.run.metadata || !("project_id" in result.run.metadata)) ||
          (result.run.metadata.project_id ?? null) !== project) {
        throw new Error("服务返回的任务关联不匹配。");
      }
      const after = useSessionStore.getState();
      const afterSession = after.sessions.find((item) => item.id === sessionId);
      if (!viewCurrent() || (after.currentProjectId ?? null) !== project ||
          (afterSession?.project_id ?? null) !== project || afterSession?.engine !== session.engine) return "uncertain";
      const currentPending = readPending(key, sessionId);
      if (currentPending?.request.idempotency_key !== value.request.idempotency_key) return "uncertain";
      localStorage.removeItem(key);
      setPending(null); setNotice("任务已接收；请在时间线查看状态。");
      void useRunTimelineStore.getState().loadForSession(sessionId);
      return "accepted";
    } catch (error) {
      if (viewCurrent()) setNotice(sent ? `尚未确认是否接收，请检查时间线或重试同一请求。` :
        `未提交：${error instanceof Error ? error.message : "无法读取或保存待确认请求"}`);
      return sent ? "uncertain" : "rejected";
    } finally {
      inFlight.delete(key);
      if (viewCurrent()) setBusy(false);
    }
  };
  const forget = () => {
    if (!sessionId || inFlight.has(keyFor(host, sessionId))) return;
    try {
      localStorage.removeItem(keyFor(host, sessionId)); setPending(null); setBlocked(false);
      setNotice("仅移除本地待确认记录；已接收的任务不会取消，请检查时间线。");
    } catch { setNotice("无法移除本地记录，请检查浏览器存储。"); }
  };
  return { pending, busy, blocked, ready, notice, submit: (input: QueuedTaskInput) => execute(input), retry: () => execute(), forget };
}
