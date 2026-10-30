import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { StreamEvent, type ProjectRunSummaryItem, type ProjectRunSummaryResult, type ProjectRunHistoryStatus, type RunStepsResult, type VerificationEvidence, type TextArtifact } from "../../types/ipc";
import { Button } from "../../ui";
import { VerificationBadge } from "../chat/VerificationBadge";
import { VerificationDetails } from "../chat/VerificationDetails";
import { RunAttachments } from "../right-panel/RunAttachments";
import { RunTextArtifacts } from "../right-panel/RunTextArtifacts";
import { FollowupRunDraft } from "../right-panel/FollowupRunDraft";
import { RunRecoveryActions } from "../right-panel/RunRecoveryActions";
import { RunChangeEvidence } from "./RunChangeEvidence";
import { ProjectRunBudget } from "./ProjectRunBudget";
import { RunDeliverySummary } from "./RunDeliverySummary";
import { ProjectRunAttention } from "./ProjectRunAttention";
import { OpenRunDeliveries } from "./OpenRunDeliveries";

type Group = "attention" | "active" | "recent";
type SearchStatus = ProjectRunHistoryStatus;
const searchLabels: Record<SearchStatus, string> = { all: "全部状态", attention: "等待处理", active: "运行与排队", failed: "失败、取消或中断", completed: "执行结束" };
const searchStatuses: Record<SearchStatus, string[]> = {
  all: ["queued", "planning", "running", "awaiting_approval", "waiting_input", "budget_paused", "completed", "failed", "cancelled", "interrupted"],
  attention: ["awaiting_approval", "waiting_input", "budget_paused", "interrupted"], active: ["queued", "planning", "running"],
  failed: ["failed", "cancelled", "interrupted"], completed: ["completed"],
};
type WorkbenchMemory = { group: Group; query: string; status: SearchStatus;
  search?: { term: string; status: SearchStatus; offset: number }; selected?: { id: string; session: string } };
const rememberedWork = new Map<string, WorkbenchMemory>();
const memoryKey = (host: string, service: string, project: string) => JSON.stringify([host, service, project]);
const defaultMemory = (): WorkbenchMemory => ({ group: "attention", query: "", status: "all" });

const groupLabels: Record<Group, string> = { attention: "等待处理", active: "运行与排队", recent: "最近结果" };
const cancellable = new Set(["queued", "planning", "running", "awaiting_approval", "waiting_input"]);
const statusLabels: Record<string, string> = { queued: "排队中", planning: "规划中", running: "运行中", awaiting_approval: "等待授权",
  waiting_input: "等待输入", budget_paused: "预算暂停", interrupted: "中断待核对", completed: "执行结束", failed: "执行失败", cancelled: "已取消" };

export function ProjectRunDashboard({ onClose, onOpenSession, onPrepareReport }: { onClose: () => void; onOpenSession: () => void; onPrepareReport: (transfer: { host: string; service: string; project: string; session: string; row: TextArtifact }) => void }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId), host = ipc.agentBaseUrl;
  const activeSession = useSessionStore((s) => s.currentSessionId);
  const name = useSessionStore((s) => s.projects.find((item) => item.id === project)?.name);
  const [group, setGroup] = useState<Group>("attention"), [service, setService] = useState<string | null>(null);
  const [groups, setGroups] = useState<ProjectRunSummaryResult["groups"]>(undefined);
  const [detail, setDetail] = useState<ProjectRunSummaryResult["detail"]>(undefined);
  const [snapshot, setSnapshot] = useState<RunStepsResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [revision, setRevision] = useState(0);
  const [query, setQuery] = useState(""), [appliedQuery, setAppliedQuery] = useState("");
  const [searchStatus, setSearchStatus] = useState<SearchStatus>("all"), [appliedStatus, setAppliedStatus] = useState<SearchStatus>("all");
  const [history, setHistory] = useState<ProjectRunSummaryResult["history"]>(undefined);
  const [historyStale, setHistoryStale] = useState(false);
  const historyService = useRef<string | null>(null), restoredScope = useRef<string | null>(null);
  const [restoreNotice, setRestoreNotice] = useState("");
  const remember = (patch: Partial<WorkbenchMemory>) => {
    if (!project || !service) return;
    const key = memoryKey(host, service, project);
    rememberedWork.set(key, { ...(rememberedWork.get(key) ?? defaultMemory()), ...patch });
  };
  const generation = useRef(0), actionEpoch = useRef(0), actionLock = useRef(false), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const preserveDetailForSession = useRef<string | null>(null);
  const [stopping, setStopping] = useState<string | null>(null), [stopNotice, setStopNotice] = useState("");
  const [requested, setRequested] = useState<Set<string>>(() => new Set());
  useEffect(() => { preserveDetailForSession.current = null; generation.current++; actionEpoch.current++; setStopping(null); setStopNotice(""); setRequested(new Set()); setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setHistory(undefined); setHistoryStale(false); historyService.current = null; restoredScope.current = null; setRestoreNotice(""); setQuery(""); setAppliedQuery(""); setSearchStatus("all"); setAppliedStatus("all"); setError(""); setBusy(false); }, [project, host]);
  useEffect(() => { generation.current++; actionEpoch.current++; setStopping(null); setStopNotice(""); setRequested(new Set()); if (preserveDetailForSession.current !== activeSession) { preserveDetailForSession.current = null; setDetail(undefined); setSnapshot(null); } setBusy(false); }, [activeSession]);
  const refresh = useCallback(async () => {
    if (!project) return;
    const ticket = ++generation.current;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError("");
    try {
      const scope = await typedIPC.listAttachments(project);
      if (!current()) return;
      if (!scope.service_id || scope.attachments.some((row) => row.project_id !== project || row.service_id !== scope.service_id)) throw new Error("项目或服务身份不匹配");
      const result = await typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: scope.service_id });
      if (!current()) return;
      if (result.project_id !== project || result.service_id !== scope.service_id || !result.groups ||
          Object.values(result.groups).some((part) => !part || !Array.isArray(part.items) || part.items.length > 25)) throw new Error("运行摘要关联不匹配");
      setService(scope.service_id); setGroups(result.groups);
      if (preserveDetailForSession.current === activeSession && service === scope.service_id) preserveDetailForSession.current = null;
      else { preserveDetailForSession.current = null; setDetail(undefined); setSnapshot(null); }
      if (historyService.current && historyService.current !== scope.service_id) {
        setHistory(undefined); historyService.current = null;
      } else if (historyService.current) setHistoryStale(true);
      const savedKey = memoryKey(host, scope.service_id, project);
      const saved = rememberedWork.get(savedKey);
      if (saved && restoredScope.current !== savedKey) {
        setGroup(saved.group); setQuery(saved.query); setSearchStatus(saved.status); setRestoreNotice("");
        const [historyResult, detailResult, stepsResult, identityResult] = await Promise.allSettled([
          saved.search ? typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: scope.service_id,
            history_query: saved.search.term, history_offset: saved.search.offset, history_status: saved.search.status }) : Promise.resolve(null),
          saved.selected ? typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: scope.service_id, run_id: saved.selected.id }) : Promise.resolve(null),
          saved.selected ? typedIPC.getRunSteps(saved.selected.id) : Promise.resolve(null),
          typedIPC.getServiceIdentity(),
        ]);
        if (!current()) return;
        if (identityResult.status !== "fulfilled" || identityResult.value.service_id !== scope.service_id) throw new Error("服务身份变化，无法恢复工作上下文");
        restoredScope.current = savedKey;
        let failed = false;
        if (saved.search) {
          const found = historyResult.status === "fulfilled" ? historyResult.value : null, page = found?.history;
          if (found?.project_id === project && found.service_id === scope.service_id && page?.offset === saved.search.offset &&
              Array.isArray(page.items) && page.items.length <= 25 && typeof page.has_more === "boolean" &&
              page.items.every((item) => searchStatuses[saved.search!.status].includes(item.status))) {
            setAppliedQuery(saved.search.term); setAppliedStatus(saved.search.status); setHistory(page); setHistoryStale(false); historyService.current = scope.service_id;
          } else { setHistory(undefined); failed = true; }
        }
        if (saved.selected) {
          const found = detailResult.status === "fulfilled" ? detailResult.value : null;
          const steps = stepsResult.status === "fulfilled" ? stepsResult.value : null;
          if (found?.project_id === project && found.service_id === scope.service_id && found.detail?.id === saved.selected.id &&
              found.detail.session_id === saved.selected.session && steps?.run.id === saved.selected.id && found.detail.status === steps.run.status &&
              steps.run.session_id === saved.selected.session && steps.run.metadata?.project_id === project &&
              Number.isSafeInteger(steps.cursor) && steps.cursor >= 0 && steps.steps.every((step) => step.run_id === saved.selected!.id && step.session_id === saved.selected!.session)) {
            setDetail(found.detail); setSnapshot(steps);
          } else { setDetail(undefined); setSnapshot(null); failed = true; }
        }
        setRestoreNotice(failed ? "部分已记住的搜索或运行无法重新确认，请重新查找。" : "已重新读取上次的任务工作上下文；结果为当前读取时快照。");
      }
    } catch (e) { if (current()) { setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setHistory(undefined); historyService.current = null; setError(e instanceof Error ? e.message : String(e)); } }
    finally { if (current()) setBusy(false); }
  }, [project, host, activeSession]);
  useEffect(() => { void refresh(); return () => { generation.current++; }; }, [refresh, revision]);
  useEffect(() => {
    const wake = () => { setHistoryStale(true); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setRevision((n) => n + 1), 250); };
    const unsubs = [StreamEvent.RunCreated, StreamEvent.RunUpdated, StreamEvent.RunCompleted].map((event) => ipc.on(event, wake));
    unsubs.push(ipc.onSideCar((event) => { generation.current++; actionEpoch.current++; setStopping(null); setRequested(new Set()); setStopNotice("连接变化；若已发送停止请求，请刷新运行状态确认结果。"); setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setHistory(undefined); setHistoryStale(false); historyService.current = null; restoredScope.current = null; setBusy(false);
      if (event.status === "started") wake(); else setError("连接中断，运行状态待刷新。"); }));
    return () => { generation.current++; actionEpoch.current++; if (timer.current) clearTimeout(timer.current); unsubs.forEach((off) => off()); };
  }, []);
  const select = async (item: ProjectRunSummaryItem, open: boolean) => {
    if (!project || !service || busy) return;
    const ticket = ++generation.current, expected = service;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError(""); setDetail(undefined); setSnapshot(null);
    try {
      const result = await typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: expected, run_id: item.id });
      if (!current()) return;
      if (result.project_id !== project || result.service_id !== expected || !result.detail || result.detail.id !== item.id || result.detail.session_id !== item.session_id) throw new Error("运行或会话归属已改变");
      let steps: RunStepsResult | null = null;
      if (!open) {
        steps = await typedIPC.getRunSteps(item.id);
        const scope = await typedIPC.listAttachments(project);
        if (!current()) return;
        if (scope.service_id !== expected || scope.attachments.some((row) => row.project_id !== project || row.service_id !== expected) ||
            steps.run.id !== item.id || steps.run.session_id !== item.session_id || steps.run.metadata?.project_id !== project ||
            steps.steps.some((step) => step.run_id !== item.id || step.session_id !== item.session_id)) throw new Error("运行、项目或服务身份不匹配");
      }
      if (open) {
        if (useSessionStore.getState().currentProjectId !== project && !useSessionStore.getState().setCurrentProject(project)) return;
        if (useSessionStore.getState().currentSessionId !== item.session_id && !useSessionStore.getState().setCurrent(item.session_id)) return;
        void useSessionStore.getState().refresh({ loadCurrent: false });
        remember({ selected: { id: item.id, session: item.session_id } });
        onOpenSession();
      } else { setDetail(result.detail); setSnapshot(steps); remember({ selected: { id: item.id, session: item.session_id } }); }
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const prepareHistoricalReport = async (row: TextArtifact) => {
    if (!project || !service || !detail || busy) return;
    const ticket = ++generation.current, expected = service, run = detail.id, session = detail.session_id;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError("");
    try {
      const [identity, result, target, artifact] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: expected, run_id: run }),
        typedIPC.getSession(session), typedIPC.getArtifact(project, row.id),
      ]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const fresh = artifact.artifact;
      if (identity.service_id !== expected || after.service_id !== expected || result.project_id !== project ||
          result.service_id !== expected || result.detail?.id !== run || result.detail.session_id !== session ||
          target.session.id !== session || target.session.project_id !== project || fresh.id !== row.id ||
          fresh.project_id !== project || fresh.service_id !== expected || fresh.run_id !== run ||
          fresh.sha256 !== row.sha256 || fresh.snapshot_id !== row.snapshot_id || fresh.version !== row.version ||
          fresh.source_path !== row.source_path || fresh.size_bytes !== row.size_bytes || fresh.mime_type !== row.mime_type)
        throw new Error("运行、会话或成果身份已变化，请重新选择。");
      if (session !== activeSession && !useSessionStore.getState().setCurrent(session)) return;
      onPrepareReport({ host, service: expected, project, session, row: fresh });
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setBusy(false); }
  };
  const searchHistory = async (term: string, offset: number, status: SearchStatus) => {
    if (!project || !service || busy) return;
    const ticket = ++generation.current, expected = service;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError(""); setHistory(undefined); setDetail(undefined); setSnapshot(null);
    try {
      const result = await typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: expected, history_query: term, history_offset: offset, history_status: status });
      if (!current()) return;
      if (result.project_id !== project || result.service_id !== expected || !result.history || result.history.offset !== offset ||
          !Array.isArray(result.history.items) || result.history.items.length > 25 || result.history.items.some((item) => !searchStatuses[status].includes(item.status)) || typeof result.history.has_more !== "boolean") throw new Error("历史运行归属不匹配");
      setAppliedQuery(term); setAppliedStatus(status); historyService.current = expected; setHistoryStale(false); setHistory(result.history);
      remember({ search: { term, status, offset }, selected: undefined });
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const stop = async (item: ProjectRunSummaryItem) => {
    if (!project || !service || actionLock.current || !cancellable.has(item.status) || item.live === false) return;
    const ticket = ++actionEpoch.current, expected = service;
    const current = () => ticket === actionEpoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    actionLock.current = true; setStopping(item.id); setStopNotice("");
    let sent = false;
    try {
      const identity = await typedIPC.getServiceIdentity();
      const snapshot = await typedIPC.getRunSteps(item.id);
      const identityAfter = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== expected || identityAfter.service_id !== expected || snapshot.run.id !== item.id ||
          snapshot.run.session_id !== item.session_id || snapshot.run.metadata?.project_id !== project ||
          snapshot.steps.some((step) => step.run_id !== item.id || step.session_id !== item.session_id)) throw new Error("运行、项目或服务身份已变化，请刷新后重试");
      if (!cancellable.has(snapshot.run.status)) throw new Error("任务已不处于可停止状态，请刷新记录");
      if (!window.confirm(`请求停止“${item.title || item.id}”？已执行的操作不会撤销；最终状态需要刷新确认。`)) return;
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (finalIdentity.service_id !== expected) throw new Error("服务身份已变化，请刷新后重试");
      sent = true;
      const reply = await typedIPC.cancelRun(item.id);
      if (!current()) return;
      if (reply.run.id !== item.id || reply.run.session_id !== item.session_id || reply.run.metadata?.project_id !== project) throw new Error("停止回执归属不匹配");
      if (reply.requested) {
        setRequested((previous) => new Set([...previous, item.id]));
        setStopNotice(`已请求停止 ${item.title || item.id}；请刷新确认最终状态。`);
      } else setStopNotice(`未发送停止请求，任务可能已经结束或当前服务无法停止；请刷新确认 ${item.title || item.id}。`);
      setRevision((n) => n + 1);
    } catch (e) {
      if (current()) setStopNotice(`${sent ? "停止结果未确认，请先刷新状态：" : "未发送停止请求："}${e instanceof Error ? e.message : String(e)}`);
    } finally { actionLock.current = false; if (current()) setStopping(null); }
  };
  const items = groups?.[group].items ?? [];
  return <section className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4" aria-label="项目任务状态">
    <div className="flex justify-between gap-2"><div><h2 className="text-lg font-semibold">项目任务状态</h2>
      <p className="break-all text-xs text-ink-2">项目：{name ?? project ?? "未选择"} · 服务：{host} · 身份：{service ?? "待确认"}</p></div><Button size="sm" variant="ghost" onClick={onClose}>关闭</Button></div>
    <p className="text-sm text-ink-1">按持久运行记录分组，不按会话计数。每组最多显示最近 25 条；已归档会话、未绑定项目的旧记录和内部子任务不在此列表。等待状态不代表审批或问题仍有效。</p>
    <div className="flex flex-wrap gap-2">{(Object.keys(groupLabels) as Group[]).map((id) => <Button key={id} type="button" size="sm" variant={group === id ? "primary" : "secondary"} aria-pressed={group === id} disabled={busy} onClick={() => { setGroup(id); setDetail(undefined); setSnapshot(null); remember({ group: id, selected: undefined }); }}>{groupLabels[id]}</Button>)}
      <Button type="button" size="sm" variant="ghost" disabled={busy || !project} onClick={() => void refresh()}>刷新</Button></div>
    <form className="flex flex-wrap items-end gap-2 rounded border border-line p-2" onSubmit={(event) => { event.preventDefault(); void searchHistory(query.trim(), 0, searchStatus); }}>
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">查找项目运行
        <input className="rounded border border-line bg-surface-0 p-1" value={query} maxLength={200} disabled={busy} onChange={(event) => { setQuery(event.target.value); remember({ query: event.target.value }); }} placeholder="运行标题、会话标题或运行 ID" />
      </label>
      <label className="flex flex-col gap-1 text-sm">状态范围<select aria-label="任务搜索状态" className="rounded border border-line bg-surface-0 p-1" value={searchStatus} disabled={busy} onChange={(event) => { const status = event.target.value as SearchStatus; setSearchStatus(status); remember({ status }); }}>
        {(Object.keys(searchLabels) as SearchStatus[]).map((status) => <option key={status} value={status}>{searchLabels[status]}</option>)}
      </select></label>
      <Button type="submit" size="sm" variant="secondary" disabled={busy || !service}>查找</Button>
      {history && <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { setHistory(undefined); historyService.current = null; remember({ search: undefined }); }}>关闭搜索结果</Button>}
      <p className="w-full text-xs text-ink-2">按状态和关键词搜索项目持久运行；空关键词浏览所选状态。等待处理包括授权、输入、预算暂停和中断；已归档会话和未绑定项目的旧记录不包含在内。</p>
    </form>
    {history && <div className="space-y-2 rounded border border-line p-2" aria-label="项目运行搜索结果">
      <p className="text-sm">搜索：{appliedQuery || "全部关键词"} · {searchLabels[appliedStatus]} · 从第 {history.offset + 1} 条记录开始</p>
      {historyStale && <p role="status" className="text-status-warning">运行记录已变化，此页是旧快照；打开结果会重新核对。<Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void searchHistory(appliedQuery, history.offset, appliedStatus)}>刷新此页</Button></p>}
      {!history.items.length && <p>本页没有可显示的运行；主机边界可能隐藏部分记录。</p>}
      <ul className="space-y-2">{history.items.map((item) => <li key={item.id} className="rounded bg-surface-2 p-2">
        <p className="font-medium">{item.title || "未命名运行"}</p>
        <p className="break-all text-xs text-ink-2">会话：{item.session_title || item.session_id} · 运行：{item.id}</p>
        <p className="text-xs">记录状态：{statusLabels[item.status] ?? item.status}{item.live === false ? " · 当前服务无法确认仍在执行" : ""} · {item.completed_at ?? item.created_at}</p>
        <div className="mt-1 flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void select(item, false)}>查看结果</Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void select(item, true)}>打开所属会话</Button></div>
      </li>)}</ul>
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || history.offset === 0} onClick={() => void searchHistory(appliedQuery, Math.max(0, history.offset - 25), appliedStatus)}>上一页</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || !history.has_more || history.offset >= 10000} onClick={() => void searchHistory(appliedQuery, history.offset + 25, appliedStatus)}>下一页</Button></div>
      {history.has_more && history.offset >= 10000 && <p className="text-xs">已到浏览上限，请缩小搜索范围。</p>}
      <p className="text-xs text-ink-2">记录可能在翻页时变化，页码不是固定快照；主机不可访问的记录不会显示。</p>
    </div>}
    {service && <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
      <span>搜索条件和所选运行只在本页面按服务地址、服务身份和项目保留；返回时重新读取。</span>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { if (project) rememberedWork.delete(memoryKey(host, service, project)); setRestoreNotice("已忘记此项目的任务工作上下文；当前页面不变。"); }}>忘记任务工作上下文</Button>
      {restoreNotice && <span role="status">{restoreNotice}</span>}
    </div>}
    {stopNotice && <p role="status" className="break-words text-sm text-status-warning">{stopNotice}</p>}
    {!project && <p role="status">请先选择项目。</p>}{busy && <p role="status">正在读取当前服务记录…</p>}{error && <p role="alert">{error}</p>}
    {!busy && groups && !items.length && <p>此范围内没有已显示的运行记录。</p>}
    {groups?.[group].truncated && <p>此类别超过 25 条，仅显示最近记录。</p>}
    <ul className="space-y-2">{items.map((item) => <li key={item.id} className="rounded border border-line p-2">
      <p className="font-medium">{item.title || "未命名运行"}</p><p className="break-all text-xs text-ink-2">会话：{item.session_title || item.session_id} · 运行：{item.id}</p>
      <p className="text-xs">记录状态：{statusLabels[item.status] ?? item.status}{item.live === false ? " · 当前服务无法确认仍在执行" : ""} · {group === "recent" ? item.completed_at ?? item.created_at : item.created_at}</p>
      <div className="mt-1 flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void select(item, false)}>查看结果</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void select(item, true)}>打开所属会话</Button>
        {cancellable.has(item.status) && item.live !== false && <Button type="button" size="sm" variant="secondary" disabled={!!stopping || requested.has(item.id) || !service} onClick={() => void stop(item)}>{stopping === item.id ? "正在请求停止…" : requested.has(item.id) ? "已请求停止" : "停止此任务"}</Button>}</div>
    </li>)}</ul>
    {detail && <div className="rounded border border-line p-3"><h3 className="font-medium">运行结果 · {detail.id}</h3>
      <p>读取时状态：{statusLabels[detail.status] ?? detail.status}</p>
      {detail.text === null ? <p>没有可确认属于此运行的已保存文本回执；进行中或旧运行可能尚无回执。</p> : <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{detail.text || "（文本为空）"}</pre>}
      {detail.text_truncated && <p>仅显示前 8000 字符；请打开所属会话查看。</p>}
      {snapshot && service && <>
        <div className="mt-2 space-y-1 rounded border border-line p-2 text-xs" aria-label="运行证据">
          <p className="font-medium">已保存的运行证据</p>
          <p>快照状态：{statusLabels[snapshot.run.status] ?? snapshot.run.status}</p>
          {snapshot.run.error && <p className="whitespace-pre-wrap break-all text-status-warning">运行错误：{snapshot.run.error.slice(0, 2000)}{snapshot.run.error.length > 2000 ? "…（已截断）" : ""}</p>}
          {["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(snapshot.run.status)
            ? <p>运行尚未结束，验证结果可能尚未保存。</p>
            : <><VerificationBadge evidence={snapshot.run.metadata?.verification as VerificationEvidence | undefined} />
              <VerificationDetails evidence={snapshot.run.metadata?.verification as VerificationEvidence | undefined} /></>}
          <p>已记录失败步骤：{snapshot.steps.filter((step) => step.status === "failed").length} 项</p>
          <ul className="space-y-1">{snapshot.steps.filter((step) => step.status === "failed").slice(0, 25).map((step) => {
            const description = step.error || step.summary;
            return <li key={step.id} className="rounded bg-surface-2 p-2"><p className="break-all">{step.title || step.tool_name || step.kind}</p>
              {description && <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all">{description.slice(0, 2000)}{description.length > 2000 ? "…（已截断）" : ""}</pre>}</li>;
          })}</ul>
          {snapshot.steps.filter((step) => step.status === "failed").length > 25 && <p>仅显示前 25 项失败步骤，请打开所属会话查看全部记录。</p>}
          <p className="text-ink-2">步骤记录与配置命令的结果不证明交付质量；运行中的快照可能发生变化。</p>
        </div>
        <OpenRunDeliveries key={`${host}:${service}:${project}:${activeSession}:${detail.session_id}:${detail.id}`}
          host={host} service={service} project={project!} session={detail.session_id} run={detail.id} />
        <ProjectRunAttention key={`${host}:${service}:${project}:${activeSession}:${detail.session_id}:${detail.id}`}
          snapshot={snapshot} host={host} service={service} project={project!} onOpenSession={onOpenSession} />
        <RunDeliverySummary key={`${host}:${service}:${project}:${activeSession}:${detail.session_id}:${detail.id}`}
          host={host} service={service} project={project!} session={detail.session_id} run={detail.id} />
        {snapshot.run.status === "budget_paused" && <ProjectRunBudget key={`${host}:${service}:${project}:${detail.session_id}:${detail.id}:${activeSession}`}
          host={host} service={service} project={project!} session={detail.session_id} run={detail.id} />}
        <RunChangeEvidence key={`${service}:${detail.id}:${detail.session_id}`} steps={snapshot.steps}
          workspace={typeof snapshot.run.metadata?.workspace_snapshot === "string" ? snapshot.run.metadata.workspace_snapshot : null} />
        <RunAttachments run={snapshot.run} steps={snapshot.steps} reviewScope={{ sessionId: detail.session_id, projectId: project!, serviceId: service }} />
        <RunTextArtifacts key={`${service}:${detail.id}:${detail.session_id}`} runId={detail.id} sessionId={detail.session_id} projectId={project!} serviceId={service} review onPrepareReport={(row) => void prepareHistoricalReport(row)} />
        <FollowupRunDraft key={`${service}:${detail.id}:${activeSession}`} run={snapshot.run} allowOtherSession />
        {(["interrupted", "failed", "cancelled", "budget_paused"].includes(snapshot.run.status) ||
          (snapshot.run.status === "completed" && snapshot.run.metadata?.truncated === true && snapshot.run.metadata?.engine !== "codex")) &&
          <div className="mt-2 rounded border border-line p-2" aria-label="历史运行接续">
            <p className="text-sm font-medium">核对并接续此运行</p>
            {snapshot.run.status === "budget_paused" && <p>先查看上方共享账本并核对未知责任，再进入原会话的受控恢复流程；不会自动恢复。</p>}
            {activeSession !== detail.session_id ? <Button type="button" size="sm" variant="secondary" onClick={() => {
              preserveDetailForSession.current = detail.session_id;
              if (!useSessionStore.getState().setCurrent(detail.session_id)) preserveDetailForSession.current = null;
            }}>切换到所属会话并核对</Button> : <RunRecoveryActions run={snapshot.run} refresh={async () => {
              const before = await typedIPC.getServiceIdentity();
              const next = await typedIPC.getRunSteps(detail.id);
              const after = await typedIPC.getServiceIdentity();
              if (ipc.agentBaseUrl !== host || useSessionStore.getState().currentSessionId !== detail.session_id ||
                  useSessionStore.getState().currentProjectId !== project || before.service_id !== service || after.service_id !== service ||
                  next.run.id !== detail.id || next.run.session_id !== detail.session_id || next.run.metadata?.project_id !== project ||
                  next.steps.some((step) => step.run_id !== detail.id || step.session_id !== detail.session_id)) throw new Error("运行、项目或服务身份已变化，请重新读取");
              setSnapshot(next);
            }} />}
          </div>}
        {!snapshot.steps.some((step) => step.status === "completed" && step.payload?.source === "run_attachment_v1" &&
          step.payload?.project_id === project && step.payload?.service_id === service && typeof step.payload?.attachment_id === "string" &&
          typeof step.payload?.sha256 === "string") && <p>没有可确认的运行关联附件；未关联或较早的项目成果不在此显示。</p>}
      </>}
      <p className="text-xs text-ink-2">记录状态不等于事实核验或交付质量确认。</p>
    </div>}
  </section>;
}
