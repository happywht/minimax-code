import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { RunGetResult, RunStepsResult, ScheduleOccurrence } from "../../types/ipc";
import { Button } from "../../ui";
import { RunAttachments } from "../right-panel/RunAttachments";

interface Detail { snapshot: RunStepsResult; receipt: RunGetResult; serviceId: string; scopeKey: string }
const labels: Record<string, string> = { queued: "排队中", planning: "规划中", running: "运行中", awaiting_approval: "等待授权",
  waiting_input: "等待输入", completed: "执行结束", failed: "执行失败", cancelled: "已取消", interrupted: "运行中断", budget_paused: "预算暂停" };

export function ScheduleRunDetail({ occurrence }: { occurrence: ScheduleOccurrence }): JSX.Element {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const activeProject = useSessionStore((state) => state.currentProjectId);
  const [open, setOpen] = useState(false), [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const project = occurrence.context?.project_id, host = ipc.agentBaseUrl;
  const scopeKey = JSON.stringify([occurrence.id, occurrence.run_id, occurrence.job_id, occurrence.revision,
    occurrence.context?.workspace, occurrence.context?.host, occurrence.context?.engine, project, host, activeSession, activeProject]);
  const eligible = !!occurrence.run_id && !!project && occurrence.context?.host === "local" && occurrence.context?.engine === "native" && !!occurrence.context?.workspace;
  useEffect(() => {
    setOpen(false); setDetail(null); setError(""); setBusy(false);
    const version = epoch;
    return () => { version.current++; };
  }, [scopeKey]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setDetail(null); setBusy(false); setError("连接变化，请重新读取本次运行。"); }), []);
  const load = async () => {
    if (!eligible || !occurrence.run_id || !project) return;
    const ticket = ++epoch.current, runId = occurrence.run_id;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    setBusy(true); setError(""); setDetail(null);
    try {
      const [snapshot, receipt, attachments] = await Promise.all([
        typedIPC.getRunSteps(runId), typedIPC.getRun(runId), typedIPC.listAttachments(project),
      ]);
      if (!current()) return;
      const matches = (run: RunStepsResult["run"]) => {
        const schedule = run.metadata?.schedule as { occurrence_id?: string; job_id?: string; revision?: number } | undefined;
        return run.id === runId && run.session_id === occurrence.session_id && run.metadata?.project_id === project &&
          run.metadata?.workspace_snapshot === occurrence.context.workspace && run.metadata?.model_snapshot?.engine === "native" &&
          schedule?.occurrence_id === occurrence.id && schedule.job_id === occurrence.job_id && schedule.revision === occurrence.revision;
      };
      if (!attachments.service_id || !matches(snapshot.run) || !matches(receipt.run) ||
          snapshot.steps.some((step) => step.run_id !== runId || step.session_id !== occurrence.session_id)) throw new Error("运行、项目或发生记录关联不一致");
      setDetail({ snapshot, receipt, serviceId: attachments.service_id, scopeKey });
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (current()) setBusy(false); }
  };
  const show = () => { if (open) { epoch.current++; setOpen(false); setDetail(null); setBusy(false); } else { setOpen(true); void load(); } };
  const view = detail?.scopeKey === scopeKey ? detail : null;
  const run = view?.snapshot.run;
  const receipt = view?.receipt.result;
  const sameStatus = !!view && view.receipt.run.status === view.snapshot.run.status;
  const text = sameStatus && receipt && "text" in receipt && receipt.session_id === occurrence.session_id && receipt.run_id === occurrence.run_id && typeof receipt.text === "string" ? receipt.text : null;
  const linked = view?.snapshot.steps.filter((step) => step.status === "completed" && step.payload?.source === "run_attachment_v1" &&
    step.payload.project_id === project && step.payload.service_id === view.serviceId) ?? [];
  return <div className="mt-1 rounded border border-line p-2" data-testid={`schedule-run-detail-${occurrence.id}`}>
    <p>执行位置：{occurrence.context?.host === "local" ? "当前服务所在主机" : "未知主机"} / {occurrence.context?.engine === "native" ? "Native" : "未知引擎"} · 项目 {project || "未知"} · 服务地址 {host}</p>
    {occurrence.context?.workspace && <p className="break-all">运行时目录：{occurrence.context.workspace}</p>}
    {!occurrence.run_id ? <p>尚未接单，没有可查看的运行或关联成果。</p> : !eligible ? <p>旧记录缺少可核实的本机、项目或目录绑定，不能直接查看关联成果。</p> : <Button type="button" size="sm" variant="secondary" onClick={show}>{open ? "收起本次运行" : "查看本次运行结果与成果"}</Button>}
    {open && <div className="space-y-1">
      {busy && <p role="status">正在读取本次运行…</p>}
      {error && <p role="alert">{error}；请刷新或打开该会话核对。</p>}
      {run && view && <>
        <p>读取时运行状态：{labels[run.status] ?? run.status} · 运行 {run.id} · 服务 {view.serviceId}</p>
        {run.error && <p>运行错误：{run.error}</p>}
        {!sameStatus && <p>读取期间运行状态发生变化，请刷新后查看回执。</p>}
        {sameStatus && (text !== null ? <><p>已保存的运行结果：</p><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words">{text.slice(0, 8000) || "（结果文本为空）"}</pre>{text.length > 8000 && <p>此处仅显示前 8000 字符；完整结果请打开会话。</p>}</> : <p>{["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) ? "运行尚未结束或结果尚未保存。" : "没有可确认归属于此运行的文本回执。"}</p>)}
        {linked.length ? <RunAttachments run={run} steps={view.snapshot.steps} reviewScope={{ sessionId: occurrence.session_id, projectId: project!, serviceId: view.serviceId }} /> : <p>没有已记录的任务关联成果；旧成果或未关联文件不会列在这里。</p>}
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>刷新本次运行</Button>
      </>}
    </div>}
  </div>;
}
