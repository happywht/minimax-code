import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useRunTimelineStore, useSessionStore } from "../../stores";
import type { RunTimelineEntry } from "../../stores/runStore";
import { Button } from "../../ui";

/** Controls individual accepted tasks without stopping the whole session. */
export function QueuedRuns({ session, host, runs, stale }: {
  session: string; host: string; runs: RunTimelineEntry[]; stale: boolean;
}): JSX.Element | null {
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setBusy(null); setNotice(""); return () => { ++epoch.current; }; }, [session, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setBusy(null); setNotice("连接已变化，请刷新后核对队列。"); }), []);
  if (!runs.length) return null;

  const cancel = async (run: RunTimelineEntry) => {
    if (lock.current || stale) return;
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === session;
    lock.current = true; setBusy(run.id); setNotice("");
    let sent = false;
    try {
      const identity = await typedIPC.getServiceIdentity();
      const snapshot = await typedIPC.getRunSteps(run.id);
      const identityAfter = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (!identity.service_id || identityAfter.service_id !== identity.service_id || snapshot.run.id !== run.id ||
          snapshot.run.session_id !== session || snapshot.run.status !== "queued" || snapshot.run.started_at ||
          snapshot.steps.some((step) => step.run_id !== run.id || step.session_id !== session)) {
        throw new Error("任务或服务状态已变化，请刷新队列");
      }
      if (!window.confirm(`取消排队任务“${run.title || run.id}”？如果它已开始，将请求停止运行；已执行的操作不会撤销。`)) return;
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (finalIdentity.service_id !== identity.service_id) throw new Error("服务身份已变化，请刷新队列");
      sent = true;
      const result = await typedIPC.cancelRun(run.id);
      if (!current()) return;
      if (result.run.id !== run.id || result.run.session_id !== session) throw new Error("取消回执关联不匹配");
      setNotice(result.requested ? "已请求取消；请刷新确认最终状态。" : "服务未接受取消请求；任务可能已结束或不可停止，请刷新确认。");
      await useRunTimelineStore.getState().loadForSession(session);
    } catch (error) {
      if (current()) setNotice(`${sent ? "取消结果未确认，请先刷新：" : "未发送取消请求："}${error instanceof Error ? error.message : String(error)}`);
    } finally { lock.current = false; if (current()) setBusy(null); }
  };

  return <div className="mt-2 space-y-2 rounded border border-line p-2" aria-label="本会话排队任务">
    <p className="font-medium">本会话排队任务 · {runs.length}</p>
    <p className="text-ink-2">列表为最近同步的活动记录；状态可能变化。可单独取消，不影响其他排队任务。</p>
    <ul className="space-y-2">{runs.map((run) => <li key={run.id} className="rounded bg-surface-2 p-2">
      <p className="break-all">{run.title || "未命名任务"} · {run.id}</p>
      {run.queuedInput && <details><summary>查看已接收内容</summary><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all">{run.queuedInput.text}</pre>{run.queuedInput.truncated && <p>内容预览已截断。</p>}</details>}
      {run.metadata?.cancel_requested === true ? <p>已请求取消，等待服务完成处理。</p> :
        <Button type="button" size="sm" variant="secondary" disabled={stale || !!busy} onClick={() => void cancel(run)}>{busy === run.id ? "正在取消…" : "取消此排队任务"}</Button>}
    </li>)}</ul>
    {notice && <p role="status" className="break-words text-status-warning">{notice}</p>}
  </div>;
}
