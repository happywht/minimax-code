import { useEffect, useState } from "react";
import { Button, Input } from "../../ui";
import { useModelStore, useSessionStore } from "../../stores";
import { VerificationBadge } from "../chat/VerificationBadge";
import { ipc, typedIPC } from "../../ipc";
import { StreamEvent, type ScheduleContext, type ScheduleOccurrence, type ScheduledJob } from "../../types/ipc";
import { ScheduleRunDetail } from "./ScheduleRunDetail";

const labels: Record<string,string> = {ready:"已绑定",needs_rebind:"需要重新绑定",blocked:"配置受阻",requires_review:"需要核对",pending:"待接单",running:"运行中",awaiting_approval:"等待授权",completed:"执行结束",budget_paused:"预算暂停",cancelled:"已取消",failed:"执行失败",interrupted:"运行中断",superseded:"已被重新绑定替代",manual:"手动",cron:"定时",chat:"对话（可写）",execute:"执行",plan:"只读规划"};
const label = (value: string) => labels[value] ?? value;
export const emptyScheduleContext = (): ScheduleContext => ({ project_id: "", provider_id: "", model: "", mode: "execute", budget_tokens: 0, timezone: "Asia/Shanghai" });
export const validScheduleContext = (context: ScheduleContext): boolean => !!(context.project_id && context.provider_id && context.model && context.budget_tokens > 0);

export function ScheduleContextFields({ value, onChange }: { value: ScheduleContext; onChange: (next: ScheduleContext) => void }): JSX.Element {
  const projects = useSessionStore((state) => state.projects);
  const models = useModelStore((state) => state.models);
  const patch = (next: Partial<ScheduleContext>) => onChange({ ...value, ...next });
  return <div className="grid grid-cols-2 gap-2 text-xs" data-testid="schedule-context-fields">
    <p className="col-span-2 text-ink-2">Local / Native · 独立会话 · 权限保存为快照，后续全局允许不扩展授权；自动任务禁用全局与插件钩子</p>
    <label>项目与目录<select aria-label="调度项目" className="w-full bg-surface-2" value={value.project_id} onChange={(event) => patch({ project_id: event.target.value })}>
      <option value="">选择项目</option>{projects.filter((project) => project.id !== "inbox" && project.root_path).map((project) => <option key={project.id} value={project.id}>{project.name} · {project.root_path}</option>)}
    </select></label>
    <label>Provider / 模型<select aria-label="调度模型" className="w-full bg-surface-2" value={`${value.provider_id}|${value.model}`} onChange={(event) => { const [provider_id, model] = event.target.value.split("|"); patch({ provider_id, model }); }}>
      <option value="|">选择模型</option>{models.filter((model) => model.provider_id).map((model) => <option key={`${model.provider_id}|${model.id}`} value={`${model.provider_id}|${model.id}`}>{model.provider} / {model.name}</option>)}
    </select></label>
    <label>单次 token 上限（必填正数）<Input aria-label="调度预算" type="number" min={1} value={value.budget_tokens || ""} onChange={(event) => patch({ budget_tokens: Number(event.target.value) })} /></label>
    <label>模式<select aria-label="调度模式" className="w-full bg-surface-2" value={value.mode} onChange={(event) => patch({ mode: event.target.value as ScheduleContext["mode"] })}>
      <option value="execute">执行</option><option value="chat">对话（可写）</option><option value="plan">只读规划</option>
    </select></label>
    <label>推理强度<Input aria-label="调度推理强度" value={value.reasoning_effort ?? ""} onChange={(event) => patch({ reasoning_effort: event.target.value || null })} /></label>
    <label>时区<Input aria-label="调度时区" value={value.timezone} onChange={(event) => patch({ timezone: event.target.value })} /></label>
  </div>;
}

export function ScheduleSnapshot({ job }: { job: ScheduledJob }): JSX.Element {
  const context = job.context;
  return <div className="px-3 text-[11px] text-ink-2" data-testid={`schedule-snapshot-${job.id}`}>
    <p>r{job.revision ?? 1} · {label(job.binding_state ?? "needs_rebind")} · {job.binding_reason}</p>
    {context && <p>Local / Native · {context.workspace} · {context.provider_id} / {context.model} · {context.reasoning_effort ?? "默认强度"} · {label(context.mode)} · {context.budget_tokens} tokens · {context.timezone}<br />权限：{context.permission_rules?.map((rule) => `${rule.tool_pattern}:${rule.action}`).join("，") || "逐次询问"} · 校验：{context.validate_command || "未配置"} · 自动任务钩子已禁用</p>}
  </div>;
}

export function ScheduleHistory({ job, expanded, onOpenSession }: { job: ScheduledJob; expanded: boolean; onOpenSession?: () => void }): JSX.Element {
  const [history, setHistory] = useState<ScheduleOccurrence[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let current = true;
    let running = false, pending = false;
    let connectionVersion = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const host = ipc.agentBaseUrl;
    setHistory([]); setError(null); setStale(false);
    if (!expanded && !job.active_occurrence_id) return () => { current = false; };
    const refresh = async () => {
      if (!current) return;
      if (running) { pending = true; return; }
      running = true;
      const version = connectionVersion;
      try {
        const result = await typedIPC.listScheduleOccurrences(job.id);
        if (current && version === connectionVersion && host === ipc.agentBaseUrl) { setHistory(result.occurrences.filter((row) => row.job_id === job.id)); setError(null); setStale(false); }
      } catch (failure) {
        if (current) { setError(String(failure)); setStale(true); }
      } finally {
        running = false;
        if (pending && current) { pending = false; void refresh(); }
      }
    };
    const wake = () => { if (timer) clearTimeout(timer); timer = setTimeout(() => void refresh(), 150); };
    const unsubs = [StreamEvent.RunUpdated, StreamEvent.RunCompleted, StreamEvent.TaskProgress, StreamEvent.ScheduleUpdated,
      StreamEvent.PermissionRequest, StreamEvent.PermissionResolved].map((event) => ipc.on(event, wake));
    unsubs.push(ipc.onSideCar((event) => {
      if (event.status === "started") wake();
      else { connectionVersion++; setStale(true); setError("连接中断，状态待刷新"); }
    }));
    void refresh();
    return () => { current = false; if (timer) clearTimeout(timer); unsubs.forEach((unsub) => unsub()); };
  }, [job.id, job.active_occurrence_id, expanded]);
  const active = history.find((occurrence) => occurrence.id === job.active_occurrence_id);
  const status = (occurrence: ScheduleOccurrence) => occurrence.status === "blocked" ? "blocked" : occurrence.execution_status ?? occurrence.status;
  const open = (occurrence: ScheduleOccurrence) => {
    if (!occurrence.context?.project_id || !occurrence.session_id) return;
    useSessionStore.getState().setCurrentProject(occurrence.context.project_id);
    useSessionStore.getState().setCurrent(occurrence.session_id);
    void useSessionStore.getState().refresh({ loadCurrent: false });
    onOpenSession?.();
  };
  return <div className="space-y-1 px-3 text-xs" data-testid={`schedule-history-${job.id}`}>
    {job.active_occurrence_id && <p>{stale ? "状态待刷新" : active ? `当前发生：${label(status(active))}` : "正在读取当前发生状态"}
      {active && !stale && status(active) === "awaiting_approval" && <Button onClick={() => open(active)}>打开会话处理授权</Button>}</p>}
    {error && <p role="status">{error}</p>}
    {expanded && !error && (history.length === 0 ? "暂无发生记录" : history.map((occurrence) => <div key={occurrence.id}>
    <p>{label(occurrence.source)} · {occurrence.scheduled_for ?? occurrence.created_at} · {label(occurrence.status === "blocked" ? "blocked" : occurrence.execution_status ?? occurrence.status)} · {occurrence.reason}</p>
    <VerificationBadge evidence={occurrence.verification} />
    {occurrence.result && <p className="whitespace-pre-wrap">{occurrence.result}</p>}
    <ScheduleRunDetail occurrence={occurrence} />
    <Button disabled={!occurrence.context?.project_id || !occurrence.session_id} onClick={() => open(occurrence)}>查看任务 {occurrence.task_id} / 运行 {occurrence.run_id ?? "尚未接单"}</Button>
  </div>))}</div>;
}
