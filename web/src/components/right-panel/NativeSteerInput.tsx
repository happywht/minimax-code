import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useRunTimelineStore, useSessionStore } from "../../stores";
import type { AgentRun } from "../../types/ipc";
import { Button } from "../../ui";

type Intent = { run_id: string; session_id: string; idempotency_key: string; text: string };

export function NativeSteerInput({ run }: { run: AgentRun }): JSX.Element | null {
  const [host, setHost] = useState(ipc.agentBaseUrl);
  useEffect(() => ipc.onSideCar(() => setHost(ipc.agentBaseUrl)), []);
  if (run.metadata?.model_snapshot?.engine !== "native" && run.metadata?.engine !== "codex") return null;
  return <Editor key={`${host}:${run.session_id}:${run.id}`} host={host} run={run} />;
}

function Editor({ run, host }: { run: AgentRun; host: string }): JSX.Element | null {
  const codex = run.metadata?.engine === "codex";
  const key = `minimax-code:steer:${encodeURIComponent(host)}:${run.session_id}:${run.id}`;
  const [intent, setIntent] = useState<Intent | null>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? "null") as Intent | null;
      return value?.run_id === run.id && value.session_id === run.session_id ? value : null;
    } catch { return null; }
  });
  const [text, setText] = useState("");
  const [open, setOpen] = useState(!!intent);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const inflight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const active = ["running", "planning", "awaiting_approval", "waiting_input"].includes(run.status);
  const current = () => mounted.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentSessionId === run.session_id;
  const submit = async () => {
    if (inflight.current || !current() || (!intent && (!active || !text.trim()))) return;
    const value = intent ?? { run_id: run.id, session_id: run.session_id, idempotency_key: crypto.randomUUID(), text };
    const project = useSessionStore.getState().currentProjectId;
    const sameContext = () => current() && useSessionStore.getState().currentProjectId === project;
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch { setNotice("本地存储不可用，未发送；无法安全重试。"); return; }
    setIntent(value); setBusy(true); setNotice(""); inflight.current = true;
    try {
      const result = await typedIPC.steerRun(value);
      if (!sameContext()) return;
      if (result.step.run_id !== run.id || result.step.session_id !== run.session_id ||
          result.step.payload?.source !== (codex ? "codex_steer" : "native_steer") || result.step.payload.text !== value.text) throw new Error("响应与请求不匹配");
      localStorage.removeItem(key); setIntent(null); setText("");
      setNotice("已确认记录；请查看下方补充状态。");
      void useRunTimelineStore.getState().loadForSession(run.session_id);
    } catch (error) {
      if (sameContext()) setNotice(`无法确认是否接收：${error instanceof Error ? error.message : String(error)}。可重试同一请求。`);
    } finally { inflight.current = false; if (current()) setBusy(false); }
  };
  if (!active && !intent && !notice) return null;
  return <div className="mt-2 text-xs">
    <button type="button" className="underline" onClick={() => setOpen(!open)}>向此 {codex ? "Codex" : "Native"} 任务补充</button>
    {open && <div className="mt-1 space-y-1">
      <p>{codex ? "发送给当前 Codex 轮次；接受不保证模型已处理。发送结果不确定时不会自动重发，请先核对任务。" : "在当前工具批次结束后的下一轮上下文加入；不会中断正在执行的工具。任务提前结束时可能未使用。"}</p>
      <textarea aria-label="运行中补充内容" className="w-full rounded border border-line bg-surface-0 p-2" rows={2}
        maxLength={8000} value={intent?.text ?? text} readOnly={!!intent} onChange={(event) => setText(event.target.value)} />
      <Button size="sm" type="button" disabled={busy || (!intent && (!active || !text.trim()))} loading={busy}
        onClick={() => void submit()}>{intent ? "重试确认同一补充" : "发送补充"}</Button>
      {intent && <button type="button" disabled={busy} className="ml-2 underline" onClick={() => {
        localStorage.removeItem(key); setIntent(null); setText(intent.text);
        setNotice("仅移除本地待确认记录；已接收的补充不会撤回。");
      }}>移除本地待确认记录</button>}
      {notice && <p role="status">{notice}</p>}
    </div>}
  </div>;
}
