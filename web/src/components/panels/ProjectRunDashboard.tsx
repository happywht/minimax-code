import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { StreamEvent, type ProjectRunSummaryItem, type ProjectRunSummaryResult, type RunStepsResult, type VerificationEvidence } from "../../types/ipc";
import { Button } from "../../ui";
import { VerificationBadge } from "../chat/VerificationBadge";
import { VerificationDetails } from "../chat/VerificationDetails";
import { RunAttachments } from "../right-panel/RunAttachments";
import { RunTextArtifacts } from "../right-panel/RunTextArtifacts";

type Group = "attention" | "active" | "recent";
const groupLabels: Record<Group, string> = { attention: "等待处理", active: "运行与排队", recent: "最近结果" };
const statusLabels: Record<string, string> = { queued: "排队中", planning: "规划中", running: "运行中", awaiting_approval: "等待授权",
  waiting_input: "等待输入", budget_paused: "预算暂停", interrupted: "中断待核对", completed: "执行结束", failed: "执行失败", cancelled: "已取消" };

export function ProjectRunDashboard({ onClose, onOpenSession }: { onClose: () => void; onOpenSession: () => void }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId), host = ipc.agentBaseUrl;
  const activeSession = useSessionStore((s) => s.currentSessionId);
  const name = useSessionStore((s) => s.projects.find((item) => item.id === project)?.name);
  const [group, setGroup] = useState<Group>("attention"), [service, setService] = useState<string | null>(null);
  const [groups, setGroups] = useState<ProjectRunSummaryResult["groups"]>(undefined);
  const [detail, setDetail] = useState<ProjectRunSummaryResult["detail"]>(undefined);
  const [snapshot, setSnapshot] = useState<RunStepsResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [revision, setRevision] = useState(0);
  const [query, setQuery] = useState(""), [appliedQuery, setAppliedQuery] = useState("");
  const [history, setHistory] = useState<ProjectRunSummaryResult["history"]>(undefined);
  const generation = useRef(0), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { generation.current++; setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setHistory(undefined); setQuery(""); setAppliedQuery(""); setError(""); setBusy(false); }, [project, host]);
  useEffect(() => { generation.current++; setDetail(undefined); setSnapshot(null); setBusy(false); }, [activeSession]);
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
      setService(scope.service_id); setGroups(result.groups); setDetail(undefined); setSnapshot(null); setHistory(undefined);
    } catch (e) { if (current()) { setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setError(e instanceof Error ? e.message : String(e)); } }
    finally { if (current()) setBusy(false); }
  }, [project, host, activeSession]);
  useEffect(() => { void refresh(); return () => { generation.current++; }; }, [refresh, revision]);
  useEffect(() => {
    const wake = () => { if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setRevision((n) => n + 1), 250); };
    const unsubs = [StreamEvent.RunCreated, StreamEvent.RunUpdated, StreamEvent.RunCompleted].map((event) => ipc.on(event, wake));
    unsubs.push(ipc.onSideCar((event) => { generation.current++; setService(null); setGroups(undefined); setDetail(undefined); setSnapshot(null); setHistory(undefined); setBusy(false);
      if (event.status === "started") wake(); else setError("连接中断，运行状态待刷新。"); }));
    return () => { generation.current++; if (timer.current) clearTimeout(timer.current); unsubs.forEach((off) => off()); };
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
        if (!useSessionStore.getState().setCurrentProject(project)) return;
        if (!useSessionStore.getState().setCurrent(item.session_id)) return;
        void useSessionStore.getState().refresh({ loadCurrent: false });
        onOpenSession();
      } else { setDetail(result.detail); setSnapshot(steps); }
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const searchHistory = async (term: string, offset: number) => {
    if (!project || !service || busy) return;
    const ticket = ++generation.current, expected = service;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host && useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError(""); setHistory(undefined); setDetail(undefined); setSnapshot(null);
    try {
      const result = await typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: expected, history_query: term, history_offset: offset });
      if (!current()) return;
      if (result.project_id !== project || result.service_id !== expected || !result.history || result.history.offset !== offset ||
          !Array.isArray(result.history.items) || result.history.items.length > 25 || typeof result.history.has_more !== "boolean") throw new Error("历史运行归属不匹配");
      setAppliedQuery(term); setHistory(result.history);
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const items = groups?.[group].items ?? [];
  return <section className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4" aria-label="项目任务状态">
    <div className="flex justify-between gap-2"><div><h2 className="text-lg font-semibold">项目任务状态</h2>
      <p className="break-all text-xs text-ink-2">项目：{name ?? project ?? "未选择"} · 服务：{host} · 身份：{service ?? "待确认"}</p></div><Button size="sm" variant="ghost" onClick={onClose}>关闭</Button></div>
    <p className="text-sm text-ink-1">按持久运行记录分组，不按会话计数。每组最多显示最近 25 条；已归档会话、未绑定项目的旧记录和内部子任务不在此列表。等待状态不代表审批或问题仍有效。</p>
    <div className="flex flex-wrap gap-2">{(Object.keys(groupLabels) as Group[]).map((id) => <Button key={id} type="button" size="sm" variant={group === id ? "primary" : "secondary"} aria-pressed={group === id} onClick={() => { setGroup(id); setDetail(undefined); setSnapshot(null); }}>{groupLabels[id]}</Button>)}
      <Button type="button" size="sm" variant="ghost" disabled={busy || !project} onClick={() => void refresh()}>刷新</Button></div>
    <form className="flex flex-wrap items-end gap-2 rounded border border-line p-2" onSubmit={(event) => { event.preventDefault(); void searchHistory(query.trim(), 0); }}>
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">查找历史运行
        <input className="rounded border border-line bg-surface-0 p-1" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} placeholder="运行标题、会话标题或运行 ID" />
      </label>
      <Button type="submit" size="sm" variant="secondary" disabled={busy || !service}>查找</Button>
      {history && <Button type="button" size="sm" variant="ghost" onClick={() => setHistory(undefined)}>关闭搜索结果</Button>}
      <p className="w-full text-xs text-ink-2">搜索已结束、失败、取消或中断的持久运行；空关键词浏览历史。已归档会话和未绑定项目的旧记录不包含在内。</p>
    </form>
    {history && <div className="space-y-2 rounded border border-line p-2" aria-label="历史运行搜索结果">
      <p className="text-sm">搜索：{appliedQuery || "全部"} · 从第 {history.offset + 1} 条记录开始</p>
      {!history.items.length && <p>本页没有可显示的运行；主机边界可能隐藏部分记录。</p>}
      <ul className="space-y-2">{history.items.map((item) => <li key={item.id} className="rounded bg-surface-2 p-2">
        <p className="font-medium">{item.title || "未命名运行"}</p>
        <p className="break-all text-xs text-ink-2">会话：{item.session_title || item.session_id} · 运行：{item.id}</p>
        <p className="text-xs">记录状态：{statusLabels[item.status] ?? item.status} · {item.completed_at ?? item.created_at}</p>
        <div className="mt-1 flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void select(item, false)}>查看结果</Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void select(item, true)}>打开所属会话</Button></div>
      </li>)}</ul>
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || history.offset === 0} onClick={() => void searchHistory(appliedQuery, Math.max(0, history.offset - 25))}>上一页</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || !history.has_more || history.offset >= 10000} onClick={() => void searchHistory(appliedQuery, history.offset + 25)}>下一页</Button></div>
      {history.has_more && history.offset >= 10000 && <p className="text-xs">已到浏览上限，请缩小搜索范围。</p>}
      <p className="text-xs text-ink-2">记录可能在翻页时变化，页码不是固定快照；主机不可访问的记录不会显示。</p>
    </div>}
    {!project && <p role="status">请先选择项目。</p>}{busy && <p role="status">正在读取当前服务记录…</p>}{error && <p role="alert">{error}</p>}
    {!busy && groups && !items.length && <p>此范围内没有已显示的运行记录。</p>}
    {groups?.[group].truncated && <p>此类别超过 25 条，仅显示最近记录。</p>}
    <ul className="space-y-2">{items.map((item) => <li key={item.id} className="rounded border border-line p-2">
      <p className="font-medium">{item.title || "未命名运行"}</p><p className="break-all text-xs text-ink-2">会话：{item.session_title || item.session_id} · 运行：{item.id}</p>
      <p className="text-xs">记录状态：{statusLabels[item.status] ?? item.status}{item.live === false ? " · 当前服务无法确认仍在执行" : ""} · {group === "recent" ? item.completed_at ?? item.created_at : item.created_at}</p>
      <div className="mt-1 flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void select(item, false)}>查看结果</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void select(item, true)}>打开所属会话</Button></div>
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
        <RunAttachments run={snapshot.run} steps={snapshot.steps} reviewScope={{ sessionId: detail.session_id, projectId: project!, serviceId: service }} />
        <RunTextArtifacts key={`${service}:${detail.id}:${detail.session_id}`} runId={detail.id} sessionId={detail.session_id} projectId={project!} serviceId={service} review />
        {!snapshot.steps.some((step) => step.status === "completed" && step.payload?.source === "run_attachment_v1" &&
          step.payload?.project_id === project && step.payload?.service_id === service && typeof step.payload?.attachment_id === "string" &&
          typeof step.payload?.sha256 === "string") && <p>没有可确认的运行关联附件；未关联或较早的项目成果不在此显示。</p>}
      </>}
      <p className="text-xs text-ink-2">记录状态不等于事实核验或交付质量确认。</p>
    </div>}
  </section>;
}
