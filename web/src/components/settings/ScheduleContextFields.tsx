import { useEffect, useRef, useState } from "react";
import { Button, Input } from "../../ui";
import { useModelStore, useScheduleStore, useSessionStore } from "../../stores";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { VerificationBadge } from "../chat/VerificationBadge";
import { ipc, typedIPC } from "../../ipc";
import { StreamEvent, type ScheduleContext, type ScheduleOccurrence, type ScheduleOccurrenceCursor, type ScheduledJob } from "../../types/ipc";
import { ScheduleRunDetail } from "./ScheduleRunDetail";

const labels: Record<string,string> = {ready:"已绑定",needs_rebind:"需要重新绑定",blocked:"配置受阻",requires_review:"需要核对",pending:"待接单",running:"运行中",awaiting_approval:"等待授权",completed:"执行结束",budget_paused:"预算暂停",cancelled:"已取消",failed:"执行失败",interrupted:"运行中断",superseded:"已被重新绑定替代",manual:"手动",cron:"定时",chat:"对话（可写）",execute:"执行",plan:"只读规划"};
const label = (value: string) => labels[value] ?? value;
// Scheduler timestamps are persisted as UTC ISO strings. Never guess the unit
// of a legacy numeric value or silently display it in the browser's timezone.
function scheduleTime(value: string | number | null | undefined, timezone?: string): string {
  if (value == null) return "未知";
  if (typeof value !== "string") return `无法解析（${value}）`;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime()) || !timezone) return `${value}（无法按计划时区显示）`;
  try {
    return `${new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(date)} ${timezone}（${value}）`;
  } catch {
    return `${value}（计划时区不可用）`;
  }
}
export const emptyScheduleContext = (): ScheduleContext => ({ project_id: "", provider_id: "", model: "", mode: "execute", budget_tokens: 0, timezone: "Asia/Shanghai" });
export const validScheduleContext = (context: ScheduleContext): boolean => !!(context.project_id && context.provider_id && context.model && typeof context.timezone === "string" && context.timezone.trim() && Number.isSafeInteger(context.budget_tokens) && context.budget_tokens > 0);

export function ScheduleContextFields({ value, onChange }: { value: ScheduleContext; onChange: (next: ScheduleContext) => void }): JSX.Element {
  const projects = useSessionStore((state) => state.projects);
  const models = useModelStore((state) => state.models);
  const patch = (next: Partial<ScheduleContext>) => onChange({ ...value, ...next });
  const projectAvailable = projects.some((project) => project.id === value.project_id && project.id !== "inbox" && !!project.root_path);
  const modelAvailable = models.some((model) => model.provider_id === value.provider_id && model.id === value.model);
  return <div className="grid grid-cols-2 gap-2 text-xs" data-testid="schedule-context-fields">
    <p className="col-span-2 text-ink-2">Local / Native · 独立会话 · 权限保存为快照，后续全局允许不扩展授权；自动任务禁用全局与插件钩子</p>
    <label>项目与目录<select aria-label="调度项目" className="w-full bg-surface-2" value={value.project_id} onChange={(event) => patch({ project_id: event.target.value })}>
      <option value="">选择项目</option>{value.project_id && !projectAvailable && <option value={value.project_id}>已保存项目当前不可用：{value.project_id}</option>}{projects.filter((project) => project.id !== "inbox" && project.root_path).map((project) => <option key={project.id} value={project.id}>{project.name} · {project.root_path}</option>)}
    </select></label>
    <label>Provider / 模型<select aria-label="调度模型" className="w-full bg-surface-2" value={`${value.provider_id}|${value.model}`} onChange={(event) => { const [provider_id, model] = event.target.value.split("|"); patch({ provider_id, model }); }}>
      <option value="|">选择模型</option>{value.provider_id && value.model && !modelAvailable && <option value={`${value.provider_id}|${value.model}`}>已保存模型当前不可用：{value.provider_id} / {value.model}</option>}{models.filter((model) => model.provider_id).map((model) => <option key={`${model.provider_id}|${model.id}`} value={`${model.provider_id}|${model.id}`}>{model.provider} / {model.name}</option>)}
    </select></label>
    {(value.project_id && !projectAvailable || value.provider_id && value.model && !modelAvailable) && <p className="col-span-2 text-status-warning">所选项目或模型当前不在可用列表中；请核对并重新选择。保存时服务端还会重新检查。</p>}
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
    <p>{job.enabled && job.binding_state === "ready" ? "持久到期游标" : "已保存的到期游标（当前不代表会运行）"}：{scheduleTime(job.next_run_at, context?.timezone)}</p>
    {job.enabled && job.binding_state === "ready" && <p>到期并不保证立即开始；当前运行未结束时不会重叠，服务恢复后多个错过周期合并为一次。</p>}
    {job.last_run_at != null && <p>最近发生状态结算：{scheduleTime(job.last_run_at, context?.timezone)}</p>}
    {context && <p>Local / Native · {context.workspace} · {context.provider_id} / {context.model} · {context.reasoning_effort ?? "默认强度"} · {label(context.mode)} · {context.budget_tokens} tokens · {context.timezone}<br />权限：{context.permission_rules?.map((rule) => `${rule.tool_pattern}:${rule.action}`).join("，") || "逐次询问"} · 校验：{context.validate_command || "未配置"} · 自动任务钩子已禁用</p>}
  </div>;
}

export function ScheduleHistory({ job, expanded, onOpenSession, serviceId, targetOccurrenceId }: { job: ScheduledJob; expanded: boolean; serviceId?: string; targetOccurrenceId?: string; onOpenSession?: () => boolean | void }): JSX.Element {
  const [history, setHistory] = useState<ScheduleOccurrence[]>([]);
  const [filter, setFilter] = useState("all");
  const [filteredHistory, setFilteredHistory] = useState<ScheduleOccurrence[]>([]);
  const [filteredCursor, setFilteredCursor] = useState<ScheduleOccurrenceCursor | null>(null);
  const [filteredBusy, setFilteredBusy] = useState(false);
  const [filteredError, setFilteredError] = useState("");
  const [filterRefresh, setFilterRefresh] = useState(0);
  const filterEpoch = useRef(0), filterPaging = useRef(false);
  const [cursor, setCursor] = useState<ScheduleOccurrenceCursor | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const targetRef = useRef<HTMLDivElement>(null);
  const autoPages = useRef(0);
  const [autoLimitReached, setAutoLimitReached] = useState(false);
  useEffect(() => { autoPages.current = 0; setAutoLimitReached(false); }, [job.id, targetOccurrenceId]);
  const pageEpoch = useRef(0), paging = useRef(false), paged = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [verified, setVerified] = useState<{ serviceId: string; status: string } | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [enabling, setEnabling] = useState(false), [reviewError, setReviewError] = useState("");
  const reviewEpoch = useRef(0), enablingRef = useRef(false);
  useEffect(() => { ++reviewEpoch.current; setVerified(null); setAcknowledged(false); setEnabling(false); setReviewError("");
    return () => { ++reviewEpoch.current; }; }, [job.id, job.revision, job.active_occurrence_id, serviceId]);
  useEffect(() => {
    let current = true;
    let running = false, pending = false;
    let connectionVersion = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const host = ipc.agentBaseUrl;
    ++pageEpoch.current; paging.current = false; paged.current = false;
    setHistory([]); setCursor(null); setLoadingMore(false); setInitialLoaded(false); setError(null); setStale(false);
    if (!expanded && !job.active_occurrence_id) return () => { current = false; };
    const refresh = async () => {
      if (!current) return;
      if (running) { pending = true; return; }
      running = true;
      const version = connectionVersion;
      try {
        const result = await typedIPC.listScheduleOccurrences(job.id);
        if (current && version === connectionVersion && host === ipc.agentBaseUrl) {
          const latest = result.occurrences.filter((row) => row.job_id === job.id);
          setHistory((previous) => {
            const ids = new Set(latest.map((row) => row.id));
            return [...latest, ...previous.filter((row) => !ids.has(row.id))];
          });
          if (!paged.current) setCursor(result.next_cursor ?? null);
          setError(null); setStale(false); setInitialLoaded(true);
        }
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
      else { connectionVersion++; ++pageEpoch.current; paging.current = false; setLoadingMore(false); ++reviewEpoch.current; setStale(true); setVerified(null); setAcknowledged(false); setError("连接中断，状态待刷新"); }
    }));
    void refresh();
    return () => { current = false; ++pageEpoch.current; if (timer) clearTimeout(timer); unsubs.forEach((unsub) => unsub()); };
  }, [job.id, job.active_occurrence_id, expanded]);
  useEffect(() => {
    const ticket = ++filterEpoch.current, host = ipc.agentBaseUrl;
    filterPaging.current = false;
    setFilteredHistory([]); setFilteredCursor(null); setFilteredError(""); setFilteredBusy(false);
    if (!expanded || filter === "all") return () => { ++filterEpoch.current; };
    setFilteredBusy(true);
    void typedIPC.listScheduleOccurrences(job.id, undefined, filter).then((result) => {
      if (ticket !== filterEpoch.current || host !== ipc.agentBaseUrl) return;
      setFilteredHistory(result.occurrences.filter((row) => row.job_id === job.id));
      setFilteredCursor(result.next_cursor ?? null);
    }).catch((failure) => {
      if (ticket === filterEpoch.current) setFilteredError(`读取筛选记录失败：${String(failure)}`);
    }).finally(() => { if (ticket === filterEpoch.current) setFilteredBusy(false); });
    const invalidate = () => { ++filterEpoch.current; setFilteredBusy(false); setFilteredError("运行状态或连接已变化，请重新读取筛选结果。"); };
    const unsubs = [StreamEvent.RunUpdated, StreamEvent.RunCompleted, StreamEvent.TaskProgress, StreamEvent.ScheduleUpdated].map((event) => ipc.on(event, invalidate));
    unsubs.push(ipc.onSideCar(invalidate));
    return () => { ++filterEpoch.current; unsubs.forEach((unsubscribe) => unsubscribe()); };
  }, [job.id, expanded, filter, filterRefresh]);
  const loadMoreFiltered = async () => {
    if (!filteredCursor || filteredBusy || filteredError || filterPaging.current) return;
    const ticket = filterEpoch.current, host = ipc.agentBaseUrl;
    filterPaging.current = true; setFilteredBusy(true);
    try {
      const result = await typedIPC.listScheduleOccurrences(job.id, filteredCursor, filter);
      if (ticket !== filterEpoch.current || host !== ipc.agentBaseUrl) return;
      setFilteredHistory((previous) => {
        const ids = new Set(previous.map((row) => row.id));
        return [...previous, ...result.occurrences.filter((row) => row.job_id === job.id && !ids.has(row.id))];
      });
      setFilteredCursor(result.next_cursor ?? null);
    } catch (failure) {
      if (ticket === filterEpoch.current) setFilteredError(`读取更早筛选记录失败：${String(failure)}`);
    } finally {
      if (ticket === filterEpoch.current) { filterPaging.current = false; setFilteredBusy(false); }
    }
  };
  const loadMore = async () => {
    if (!cursor || stale || paging.current) return;
    const ticket = pageEpoch.current, host = ipc.agentBaseUrl;
    paging.current = true; paged.current = true; setLoadingMore(true); setError(null);
    try {
      const result = await typedIPC.listScheduleOccurrences(job.id, cursor);
      if (ticket !== pageEpoch.current || host !== ipc.agentBaseUrl) return;
      const older = result.occurrences.filter((row) => row.job_id === job.id);
      setHistory((previous) => {
        const ids = new Set(previous.map((row) => row.id));
        return [...previous, ...older.filter((row) => !ids.has(row.id))];
      });
      paged.current = true; setCursor(result.next_cursor ?? null);
    } catch (failure) {
      if (ticket === pageEpoch.current) setError(`读取更早记录失败：${String(failure)}`);
    } finally {
      if (ticket === pageEpoch.current) { paging.current = false; setLoadingMore(false); }
    }
  };
  useEffect(() => {
    if (!expanded || !targetOccurrenceId || !initialLoaded || stale || error) return;
    if (history.some((row) => row.id === targetOccurrenceId)) {
      targetRef.current?.scrollIntoView({ block: "center" });
    } else if (cursor && !loadingMore && !autoLimitReached) {
      // The initial page plus at most nine automatic older-page requests.
      if (autoPages.current >= 9) { setAutoLimitReached(true); return; }
      autoPages.current++;
      void loadMore();
    }
  }, [expanded, targetOccurrenceId, initialLoaded, history, cursor, loadingMore, stale, error, autoLimitReached]);
  const active = history.find((occurrence) => occurrence.id === job.active_occurrence_id);
  const status = (occurrence: ScheduleOccurrence) => occurrence.status === "blocked" ? "blocked" : occurrence.execution_status ?? occurrence.status;
  const target = history.find((row) => row.id === targetOccurrenceId);
  const visibleHistory = filter === "all" ? history : target && !filteredHistory.some((row) => row.id === target.id) ? [target, ...filteredHistory] : filteredHistory;
  const terminalReview = verified && ["failed", "cancelled", "interrupted", "budget_paused"].includes(verified.status);
  const enableFuture = async () => {
    if (!active || !serviceId || !verified || !terminalReview || !acknowledged || stale || enabling || enablingRef.current || !job.revision ||
        verified.serviceId !== serviceId || active.id !== job.active_occurrence_id || !active.run_id) return;
    const ticket = reviewEpoch.current, host = ipc.agentBaseUrl;
    const current = () => ticket === reviewEpoch.current && ipc.agentBaseUrl === host;
    enablingRef.current = true; setEnabling(true); setReviewError("");
    try {
      const accepted = await requestConfirmation({ title: "启用未来定时计划？",
        description: "这会解除当前发生的阻塞并允许未来按当前计划执行，不会恢复或重放此运行。请确认已核对其步骤、文件和可能的副作用。",
        confirmLabel: "启用未来计划" });
      if (!accepted || !current()) return;
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== serviceId) throw new Error("服务身份已变化，请重新核对。");
      const latest = useScheduleStore.getState().jobs.find((item) => item.id === job.id);
      if (!latest || latest.revision !== job.revision || latest.active_occurrence_id !== active.id || latest.binding_state !== "requires_review")
        throw new Error("计划或发生记录已变化，请刷新并重新核对。");
      const result = await useScheduleStore.getState().enable(job.id, active.id, job.revision, serviceId);
      if (!current()) return;
      if (!result.job) throw new Error(result.error || "请刷新并核对当前状态");
      setAcknowledged(false); setVerified(null);
    } catch (failure) {
      if (current()) { setAcknowledged(false); setVerified(null); setReviewError(`启用未确认：${failure instanceof Error ? failure.message : String(failure)}。请刷新计划和本次运行后再操作。`); }
    } finally { enablingRef.current = false; if (current()) setEnabling(false); }
  };
  const open = (occurrence: ScheduleOccurrence) => {
    if (!occurrence.context?.project_id || !occurrence.session_id) return;
    if (onOpenSession?.() === false) return;
    if (!useSessionStore.getState().setCurrentProject(occurrence.context.project_id)) return;
    if (!useSessionStore.getState().setCurrent(occurrence.session_id)) return;
    void useSessionStore.getState().refresh({ loadCurrent: false });
  };
  return <div className="space-y-1 px-3 text-xs" data-testid={`schedule-history-${job.id}`}>
    {job.active_occurrence_id && <p>{stale ? "状态待刷新" : active ? `当前发生：${label(status(active))}` : "正在读取当前发生状态"}
      {active && !stale && status(active) === "awaiting_approval" && <Button onClick={() => open(active)}>打开会话处理授权</Button>}</p>}
    {error && <p role="status">{error}</p>}
    {expanded && targetOccurrenceId && initialLoaded && !history.some((row) => row.id === targetOccurrenceId) && <p role="status">{stale || error ? "无法确认通知关联的发生记录，请刷新后重试。" : autoLimitReached && cursor ? "已自动查找 10 页，请手动加载更早记录。" : cursor || loadingMore ? "正在查找通知关联的发生记录…" : "通知关联的发生记录已不可用。"}</p>}
    {job.binding_state === "requires_review" && <div className="space-y-2 rounded border border-status-warning/30 bg-surface-1 p-2">
      <p className="font-medium text-status-warning">计划已暂停，需要核对本次发生</p>
      <p>启用未来计划与继续原运行是不同操作。启用只允许未来按当前计划运行，不自动恢复或重放原步骤；继续原运行请打开其会话并核对后使用恢复入口。</p>
      {stale ? <p>连接状态待刷新，暂不能处理。</p> : active ? <>
        <p className="break-all">发生 {active.id} · 计划快照版本 {active.revision} · 状态 {label(status(active))} · 运行 {active.run_id ?? "尚未接单"}</p>
        {active.run_id ? <ScheduleRunDetail occurrence={active} onVerified={(value) => { setVerified(value); setAcknowledged(false); }} /> : <p>该发生未绑定运行；请查看原因并重新绑定计划，不能在此确认运行副作用。</p>}
        {active.run_id && <Button type="button" size="sm" variant="secondary" onClick={() => open(active)}>打开原会话核对步骤</Button>}
        {verified && verified.serviceId !== serviceId && <p role="alert">服务身份不匹配，请刷新后重新核对。</p>}
        {verified && !terminalReview && <p role="status">读取的运行状态不是可核对的终态；请刷新后处理。</p>}
        <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={!verified || !terminalReview || verified.serviceId !== serviceId || enabling} onChange={(event) => setAcknowledged(event.target.checked)} />已核对本次运行的步骤、文件和可能副作用，理解启用后仅允许未来计划执行。</label>
        <Button type="button" size="sm" disabled={!acknowledged || !terminalReview || !serviceId || !job.revision || enabling} loading={enabling} onClick={() => void enableFuture()}>确认并启用未来计划</Button>
        {reviewError && <p role="alert" className="break-words text-status-warning">{reviewError}</p>}
      </> : <p>当前发生尚未确认；请刷新，不要重复执行。</p>}
    </div>}
    {expanded && <div className="flex flex-wrap items-center gap-2 py-1">
      <label>筛选运行历史 <select aria-label="筛选定时运行历史" className="bg-surface-2" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">全部状态</option><option value="attention">需要处理</option><option value="failed">失败、中断或取消</option><option value="completed">执行结束</option><option value="active">待接单或运行中</option><option value="other">其他状态</option>
      </select></label>
      <span role="status">已显示 {visibleHistory.length} 条{(filter === "all" ? cursor : filteredCursor) ? "；可加载更早记录" : ""}{target && filter !== "all" ? "；通知关联记录始终显示" : ""}</span>
      {filter !== "all" && filteredBusy && <span role="status">正在读取…</span>}
      {filter !== "all" && filteredError && <><span role="alert">{filteredError}</span><Button type="button" size="sm" onClick={() => setFilterRefresh((value) => value + 1)}>重新读取筛选结果</Button></>}
    </div>}
    {expanded && (visibleHistory.length === 0 ? (filter === "all" ? (error ? "无法读取发生记录" : "暂无发生记录") : filteredBusy || filteredError ? null : "没有匹配的发生记录。") : visibleHistory.map((occurrence) => <div key={occurrence.id} ref={occurrence.id === targetOccurrenceId ? targetRef : undefined} className={occurrence.id === targetOccurrenceId ? "rounded border border-accent p-2" : undefined}>
    {occurrence.id === targetOccurrenceId && <p className="font-medium">通知关联的发生记录</p>}
    <p>{label(occurrence.source)} · {occurrence.scheduled_for ?? occurrence.created_at} · {label(occurrence.status === "blocked" ? "blocked" : occurrence.execution_status ?? occurrence.status)} · {occurrence.reason}</p>
    {occurrence.source === "cron" && <p>最早到期：{scheduleTime(occurrence.scheduled_for, occurrence.context?.timezone)}；合并检查截止：{scheduleTime(occurrence.coalesced_through, occurrence.context?.timezone)}。截止时间是接单时的检查边界，不表示每个周期都实际执行。</p>}
    <VerificationBadge evidence={occurrence.verification} />
    {occurrence.result && <p className="whitespace-pre-wrap">{occurrence.result}</p>}
    {!(job.binding_state === "requires_review" && occurrence.id === active?.id) && <ScheduleRunDetail occurrence={occurrence} />}
    <Button disabled={!occurrence.context?.project_id || !occurrence.session_id} onClick={() => open(occurrence)}>查看任务 {occurrence.task_id} / 运行 {occurrence.run_id ?? "尚未接单"}</Button>
  </div>))}
    {expanded && filter === "all" && cursor && <Button type="button" size="sm" disabled={stale || loadingMore} loading={loadingMore} onClick={() => void loadMore()}>加载更早记录</Button>}
    {expanded && filter !== "all" && filteredCursor && <Button type="button" size="sm" disabled={!!filteredError || filteredBusy} loading={filteredBusy} onClick={() => void loadMoreFiltered()}>加载更早匹配记录</Button>}
  </div>;
}
