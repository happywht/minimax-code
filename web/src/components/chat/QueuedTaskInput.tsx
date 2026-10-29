import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useRunTimelineStore, useSessionStore } from "../../stores";
import type { RunStartParams } from "../../types/ipc";
import { Button } from "../../ui";
import { MAX_INPUT_CHARS } from "./constants";

type Pending = { request: RunStartParams; project: string | null; draft: string };

/** A separate durable request, never an in-flight steer. */
export function QueuedTaskInput({ sessionId, budgetTokens, budgetValid = true }: { sessionId: string | null; budgetTokens?: number; budgetValid?: boolean }): JSX.Element | null {
  const [host, setHost] = useState(ipc.agentBaseUrl);
  useEffect(() => ipc.onSideCar(() => setHost(ipc.agentBaseUrl)), []);
  if (!sessionId) return null;
  return <QueuedTaskEditor key={`${host}:${sessionId}`} host={host} sessionId={sessionId} budgetTokens={budgetTokens} budgetValid={budgetValid} />;
}

function QueuedTaskEditor({ sessionId, host, budgetTokens, budgetValid }: { sessionId: string; host: string; budgetTokens?: number; budgetValid: boolean }): JSX.Element {
  const storageKey = `minimax-code:queued-input:${encodeURIComponent(host)}:${encodeURIComponent(sessionId)}`;
  const pendingKey = `${storageKey}:pending`;
  const [text, setText] = useState(() => { try { return localStorage.getItem(storageKey) ?? ""; } catch { return ""; } });
  const [pending, setPending] = useState<Pending | null>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(pendingKey) ?? "null") as Pending | null;
      return value?.request.session_id === sessionId && typeof value.request.idempotency_key === "string" ? value : null;
    } catch { return null; }
  });
  const [open, setOpen] = useState(!!text || !!pending);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const inFlight = useRef(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const current = () => live.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentSessionId === sessionId;

  const submit = async () => {
    if (inFlight.current) return;
    const state = useSessionStore.getState();
    const project = state.currentProjectId ?? null;
    const value = pending ?? { project, draft: text, request: {
      session_id: sessionId, content: text.trim(), idempotency_key: crypto.randomUUID(),
      engine: state.sessions.find((s) => s.id === sessionId)?.engine ?? "native",
      mode: useChat.getState().executionMode,
      ...(budgetTokens !== undefined ? { budget_tokens: budgetTokens } : {}),
      ...(useChat.getState().captureFileChanges ? { capture_file_changes: true } : {}),
    } };
    if (!pending && (!text.trim() || text.length > MAX_INPUT_CHARS || !budgetValid)) return;
    if (value.project !== project || !current()) {
      setNotice("会话或项目已切换；请回到原项目核对待确认请求。"); return;
    }
    // Persist the retry identity before the request can reach the service.
    try { localStorage.setItem(pendingKey, JSON.stringify(value)); }
    catch { setNotice("浏览器存储不可用，无法安全保留重试凭据；未提交。"); return; }
    setPending(value); setBusy(true); setNotice(null); inFlight.current = true;
    const sameContext = () => current() && (useSessionStore.getState().currentProjectId ?? null) === value.project;
    try {
      const result = await typedIPC.startRun(value.request);
      if (!sameContext()) return;
      if (result.run.session_id !== sessionId || result.run.request_key !== value.request.idempotency_key) {
        throw new Error("服务响应的任务关联不匹配");
      }
      const savedDraft = localStorage.getItem(storageKey);
      if (savedDraft === value.draft) localStorage.removeItem(storageKey);
      localStorage.removeItem(pendingKey);
      setPending(null); setText(savedDraft === value.draft ? "" : savedDraft ?? "");
      setNotice("任务已接收；请在时间线查看排队和运行状态。");
      void useRunTimelineStore.getState().loadForSession(sessionId);
    } catch (error) {
      if (sameContext()) setNotice(`尚未确认是否接收：${error instanceof Error ? error.message : String(error)}。请检查时间线，或重试同一请求。`);
    } finally { inFlight.current = false; if (current()) setBusy(false); }
  };

  return <div className="px-2 py-1 text-xs">
    <button type="button" aria-expanded={open} className="rounded px-1 py-1 text-ink-1 underline decoration-line-strong underline-offset-2 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => setOpen(!open)}>{open ? "收起排队任务" : "排队一个新任务"}</button>
    {open && <div className="mt-2 space-y-2">
      <p>这是独立任务，会在同会话前面的任务之后执行；不向当前任务中途追加。没有前序任务时会立即开始。停止当前会话会同时取消其排队任务。</p>
      <p>此任务 token 上限：{pending ? pending.request.budget_tokens ?? "服务默认（接收时确定）" : !budgetValid ? "输入无效" : budgetTokens ?? "服务默认（接收时确定）"}；{pending ? "重试使用原接单快照。" : "提交时固定，之后修改输入不会改变已接收任务。"}</p>
      <textarea aria-label="排队任务内容" value={pending ? String(pending.request.content) : text}
        readOnly={!!pending} maxLength={MAX_INPUT_CHARS} rows={3}
        className="w-full rounded border border-line bg-surface-0 p-2"
        onChange={(event) => { setText(event.target.value); try { localStorage.setItem(storageKey, event.target.value); } catch { /* admission checks storage */ } }} />
      <Button type="button" size="sm" disabled={busy || (!pending && (!text.trim() || !budgetValid))} loading={busy}
        onClick={() => void submit()}>{pending ? "重试确认同一任务" : "提交排队任务"}</Button>
      {pending && <button type="button" disabled={busy} className="ml-2 underline" onClick={() => {
        localStorage.removeItem(pendingKey); setPending(null);
        setNotice("仅移除本地待确认记录；已接收的任务不会取消，请检查时间线。");
      }}>移除本地待确认记录</button>}
      {notice && <p role="status">{notice}</p>}
    </div>}
  </div>;
}
