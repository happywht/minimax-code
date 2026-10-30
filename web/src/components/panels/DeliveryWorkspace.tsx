import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { AgentRun, AgentRunStep, AttachmentOriginal, TextArtifact } from "../../types/ipc";
import { useArtifactStore, useAttachmentStore, useRunTimelineStore, useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import { Button } from "../../ui";
import { RunAttachments } from "../right-panel/RunAttachments";
import { RunTextArtifacts } from "../right-panel/RunTextArtifacts";
import { FollowupRunDraft } from "../right-panel/FollowupRunDraft";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { AttachmentsPanel, type RetainedReportDraft } from "./AttachmentsPanel";
import { OfficeDeliveryPanel, type TableDraft } from "./OfficeDeliveryPanel";
import { ResearchSourcesPanel } from "./ResearchSourcesPanel";

export type DeliveryTab = "artifacts" | "attachments" | "office" | "research" | "run-artifacts";
const tabs: Array<{ id: DeliveryTab; label: string }> = [
  { id: "artifacts", label: "文本成果" }, { id: "attachments", label: "附件与报告" },
  { id: "office", label: "办公表格" }, { id: "research", label: "研究来源" },
  { id: "run-artifacts", label: "当前会话成果" },
];
const officeDrafts = new Map<string, TableDraft>();
const reportDrafts = new Map<string, RetainedReportDraft>();
export type HistoricalReportTransfer = { host: string; service: string; project: string; session: string; row: TextArtifact };
export type ReportTableTransfer = { rows: string[][]; source: string };
export type RunAttachmentTransfer = { attachment: AttachmentOriginal; runId: string };
type Binding = { project: string; session: string | null; host: string; service: string };

export function DeliveryWorkspace({ tab, onSelect, onClose, onEdit, historicalTransfer, onClearHistoricalTransfer, runTarget, onClearRunTarget }: { tab: DeliveryTab; onSelect: (tab: DeliveryTab) => void; onClose: () => void; onEdit?: (kind: "report" | "table") => void; historicalTransfer?: HistoricalReportTransfer | null; onClearHistoricalTransfer?: () => void; runTarget?: { host: string; service: string; project: string; session: string; run: string } | null; onClearRunTarget?: () => void }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId), session = useSessionStore((s) => s.currentSessionId);
  const projectName = useSessionStore((s) => s.projects.find((item) => item.id === project)?.name);
  const host = ipc.agentBaseUrl;
  const [binding, setBinding] = useState<Binding | null>(null), bindingRef = useRef<Binding | null>(null);
  const [artifactTransfers, setArtifactTransfers] = useState<Record<string, { row: TextArtifact; requireRun: boolean }>>({});
  const [tableTransfer, setTableTransfer] = useState<{ scope: string; value: ReportTableTransfer } | null>(null);
  const [researchTransfer, setResearchTransfer] = useState<{ scope: string; ids: string[] } | null>(null);
  const [attachmentTransfers, setAttachmentTransfers] = useState<Record<string, RunAttachmentTransfer>>({});
  const [ready, setReady] = useState(false), [error, setError] = useState("");
  const [visited, setVisited] = useState<Set<DeliveryTab>>(() => new Set([tab]));
  const visitedRef = useRef(visited); visitedRef.current = visited;
  const tabRef = useRef(tab); tabRef.current = tab;
  const surfaceRef = useRef<HTMLDivElement>(null);
  const epoch = useRef(0);
  const same = (item: Binding | null) => item?.project === project && item.session === session && item.host === host;
  const validBinding = same(binding) ? binding : null;
  const refreshScope = useCallback(async (recover: boolean) => {
    const ticket = ++epoch.current;
    setReady(false); setError("");
    if (!project) { setBinding(null); bindingRef.current = null; return; }
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
    try {
      const result = await typedIPC.listAttachments(project);
      if (!current()) return;
      if (!result.service_id || result.attachments.some((item) => item.project_id !== project || item.service_id !== result.service_id)) throw new Error("项目或服务身份不匹配");
      const next: Binding = { project, session, host, service: result.service_id }, previous = bindingRef.current;
      const unchanged = !!previous && previous.project === project && previous.session === session && previous.host === host && previous.service === next.service;
      if (!unchanged) setVisited(new Set([tabRef.current]));
      bindingRef.current = next; setBinding(next); setReady(true);
      if (recover && unchanged) {
        if (visitedRef.current.has("artifacts")) void useArtifactStore.getState().activate(project);
        if (visitedRef.current.has("attachments")) void useAttachmentStore.getState().activate(project);
        if (visitedRef.current.has("office")) void useOfficeDeliveryStore.getState().activate(project);
      }
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [project, session, host]);
  useEffect(() => { void refreshScope(false); return () => { epoch.current++; }; }, [refreshScope]);
  useEffect(() => {
    const off = ipc.onSideCar((event) => {
      epoch.current++; setReady(false);
      useArtifactStore.getState().clear(); useAttachmentStore.getState().clear(); useOfficeDeliveryStore.getState().clear();
      if (event.status === "started") void refreshScope(true);
      else setError("服务连接中断，交付物暂不可操作；重连后重新确认身份。");
    });
    return () => { epoch.current++; off(); };
  }, [refreshScope]);
  useEffect(() => { setVisited((current) => current.has(tab) ? current : new Set([...current, tab])); }, [tab]);
  const switchTab = (next: DeliveryTab) => { setVisited((current) => new Set([...current, next])); onSelect(next); };
  const historicalMatches = !!historicalTransfer && !!validBinding && ready &&
    historicalTransfer.host === validBinding.host && historicalTransfer.service === validBinding.service &&
    historicalTransfer.project === validBinding.project && historicalTransfer.session === validBinding.session &&
    historicalTransfer.row.project_id === validBinding.project && historicalTransfer.row.service_id === validBinding.service;
  const scopeKey = validBinding ? JSON.stringify([validBinding.host, validBinding.project, validBinding.session, validBinding.service]) : "";
  const pendingArtifact = historicalMatches ? { row: historicalTransfer!.row, requireRun: true } : artifactTransfers[scopeKey] ?? null;
  useEffect(() => {
    const node = surfaceRef.current;
    if (!node) return;
    if (ready) node.removeAttribute("inert"); else node.setAttribute("inert", "");
  }, [ready, scopeKey]);
  return <section className="flex h-full min-h-0 flex-col" aria-label="交付物工作区">
    <header className="shrink-0 space-y-2 border-b border-line p-3">
      <div className="flex items-start justify-between gap-2"><div><h2 className="font-semibold">交付物工作区</h2>
        <p className="break-all text-xs text-ink-2">项目：{projectName ?? project ?? "未选择"} · 会话：{session ?? "未选择"} · 服务地址：{host} · 服务身份：{ready ? validBinding?.service ?? "未知" : "待确认"}</p></div>
        <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button></div>
      <nav aria-label="交付物类型" className="flex flex-wrap gap-1">{tabs.map((item) => <Button key={item.id} type="button" size="sm"
        variant={item.id === tab ? "primary" : "ghost"} aria-current={item.id === tab ? "page" : undefined} onClick={() => switchTab(item.id)}>{item.label}</Button>)}</nav>
      <p className="text-xs text-ink-2">不同类型保存在各自的项目记录中；仅当前会话的明确关联显示为任务成果。内容和版式需自行核对。办公表格和报告编辑草稿在当前页面内按服务地址、服务身份、项目和会话分别保留；只有返回相同身份才会恢复。报告的研究来源需要重新选择并核对引用；待上传文件也仅在相同身份的页面内保留，需手动上传。刷新或关闭页面会丢失内存草稿和待上传文件，请先导出草稿文件并重新选择文件。</p>
      {ready && historicalTransfer && !historicalMatches && <p role="alert" className="text-xs text-status-warning">待复用成果的项目、会话或服务已变化，请返回任务看板重新选择。<button type="button" onClick={onClearHistoricalTransfer}>清除</button></p>}
      {!ready && <p role="status" className="text-xs text-status-warning">{error || (!project ? "请先选择项目。" : "正在确认当前服务身份…")}</p>}
      {!!error && project && <Button type="button" size="sm" variant="secondary" onClick={() => void refreshScope(true)}>重新确认</Button>}
    </header>
    {validBinding && <div key={scopeKey} className="relative min-h-0 flex-1">
      {!ready && <div className="absolute inset-0 z-10 bg-surface-overlay/90" aria-hidden="true" />}
      <div ref={surfaceRef} className="h-full" aria-hidden={!ready || undefined} onChangeCapture={(event) => {
        const target = event.target as HTMLElement;
        if ((tab === "attachments" || tab === "office") && !target.getAttribute("aria-label")?.startsWith("搜索")) onEdit?.(tab === "office" ? "table" : "report");
      }} onClickCapture={(event) => {
        const button = (event.target as HTMLElement).closest("button");
        const label = button?.textContent ?? "";
        if ((tab === "attachments" || tab === "office") && /添加|加入|追加|删除|新增|插入|移动|转换|恢复|撤销|重做|复制|替换/.test(label)) onEdit?.(tab === "office" ? "table" : "report");
      }}>
        {visited.has("artifacts") && <div className="h-full" hidden={tab !== "artifacts"}><ArtifactsPanel embedded onClose={onClose} reportService={validBinding.service} reportTransferPending={!!pendingArtifact} onPrepareReport={(row) => { setArtifactTransfers((previous) => ({ ...previous, [scopeKey]: { row, requireRun: false } })); switchTab("attachments"); }} /></div>}
        {visited.has("attachments") && <div className="h-full" hidden={tab !== "attachments"}><AttachmentsPanel embedded onClose={onClose} initialDraft={reportDrafts.get(scopeKey)} onRetainDraft={(draft) => reportDrafts.set(scopeKey, draft)} tableTransfer={tableTransfer?.scope === scopeKey ? tableTransfer.value : null} onClearTableTransfer={() => setTableTransfer(null)} researchTransfer={researchTransfer?.scope === scopeKey ? researchTransfer.ids : null} onClearResearchTransfer={() => setResearchTransfer(null)} attachmentTransfer={attachmentTransfers[scopeKey] ?? null} onClearAttachmentTransfer={() => setAttachmentTransfers((previous) => { const next = { ...previous }; delete next[scopeKey]; return next; })} onTableApplied={() => onEdit?.("report")} artifactTransfer={pendingArtifact?.row ?? null} artifactTransferRequiresRun={pendingArtifact?.requireRun ?? true} onClearArtifactTransfer={() => { if (historicalMatches) onClearHistoricalTransfer?.(); else setArtifactTransfers((previous) => { const next = { ...previous }; delete next[scopeKey]; return next; }); }} /></div>}
        {visited.has("office") && <div className="h-full" hidden={tab !== "office"}><OfficeDeliveryPanel embedded onClose={onClose} onText={() => switchTab("artifacts")} initialDraft={officeDrafts.get(scopeKey)} onRetainDraft={(draft) => officeDrafts.set(scopeKey, draft)} tableTransferPending={tableTransfer?.scope === scopeKey} onPrepareReportTable={(value) => { setTableTransfer({ scope: scopeKey, value }); switchTab("attachments"); }} /></div>}
        {visited.has("research") && <div className="h-full" hidden={tab !== "research"}><ResearchSourcesPanel embedded onClose={onClose} onText={() => switchTab("artifacts")} reportService={validBinding.service} reportTransferPending={researchTransfer?.scope === scopeKey} onPrepareReport={(ids) => { setResearchTransfer({ scope: scopeKey, ids }); switchTab("attachments"); }} /></div>}
        {visited.has("run-artifacts") && <div className="h-full overflow-auto p-4" hidden={tab !== "run-artifacts"}><SessionDeliveries binding={validBinding} target={runTarget} onClearTarget={onClearRunTarget} attachmentTransferPending={!!attachmentTransfers[scopeKey]} onPrepareAttachment={(value) => { setAttachmentTransfers((previous) => ({ ...previous, [scopeKey]: value })); switchTab("attachments"); }} onPrepareReport={pendingArtifact ? undefined : (row) => { setArtifactTransfers((previous) => ({ ...previous, [scopeKey]: { row, requireRun: true } })); switchTab("attachments"); }} /></div>}
      </div>
    </div>}
  </section>;
}

function SessionDeliveries({ binding, target, onClearTarget, onPrepareReport, onPrepareAttachment, attachmentTransferPending }: { binding: Binding; target?: { host: string; service: string; project: string; session: string; run: string } | null; onClearTarget?: () => void; onPrepareReport?: (row: TextArtifact) => void; onPrepareAttachment: (value: RunAttachmentTransfer) => void; attachmentTransferPending: boolean }): JSX.Element {
  const runs = useRunTimelineStore((s) => s.runs), order = useRunTimelineStore((s) => s.order);
  const loading = useRunTimelineStore((s) => s.loading), error = useRunTimelineStore((s) => s.error);
  const load = useRunTimelineStore((s) => s.loadForSession);
  const [targetRun, setTargetRun] = useState<(AgentRun & { steps: AgentRunStep[] }) | null>(null);
  const [targetBusy, setTargetBusy] = useState(false), [targetError, setTargetError] = useState("");
  const [deliveryKind, setDeliveryKind] = useState<"all" | "attachments" | "text">("all"), [deliveryQuery, setDeliveryQuery] = useState("");
  const targetEpoch = useRef(0);
  useEffect(() => { void load(binding.session); }, [binding.session, load]);
  const targetMatches = !!target && target.host === binding.host && target.service === binding.service && target.project === binding.project && target.session === binding.session;
  const loadTarget = useCallback(async () => {
    if (!target || !targetMatches || !binding.session) return;
    const ticket = ++targetEpoch.current;
    const current = () => ticket === targetEpoch.current && ipc.agentBaseUrl === binding.host &&
      useSessionStore.getState().currentProjectId === binding.project && useSessionStore.getState().currentSessionId === binding.session;
    setTargetRun(null); setTargetBusy(true); setTargetError("");
    try {
      const [identity, snapshot, owner, summary] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getRunSteps(target.run), typedIPC.getSession(binding.session),
        typedIPC.getProjectRunSummary({ project_id: binding.project, expected_service_id: binding.service, run_id: target.run }),
      ]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== binding.service || after.service_id !== binding.service ||
          owner.session.id !== binding.session || owner.session.project_id !== binding.project || owner.session.archived ||
          summary.project_id !== binding.project || summary.service_id !== binding.service || summary.detail?.id !== target.run ||
          summary.detail.session_id !== binding.session || snapshot.run.id !== target.run || snapshot.run.session_id !== binding.session ||
          snapshot.run.metadata?.project_id !== binding.project || !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
          snapshot.steps.some((step) => step.run_id !== target.run || step.session_id !== binding.session))
        throw new Error("服务、项目、会话或运行关联已变化，请重新选择任务。");
      setTargetRun({ ...snapshot.run, steps: snapshot.steps });
    } catch (reason) { if (current()) setTargetError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setTargetBusy(false); }
  }, [target, targetMatches, binding]);
  useEffect(() => {
    ++targetEpoch.current; setTargetRun(null); setTargetError(""); setTargetBusy(false);
    if (targetMatches) void loadTarget();
    return () => { ++targetEpoch.current; };
  }, [loadTarget, targetMatches]);
  useEffect(() => ipc.onSideCar(() => { ++targetEpoch.current; setTargetRun(null); setTargetBusy(false); setTargetError("连接已变化，请重新读取任务。"); }), []);
  const associated = order.map((id) => runs[id]).filter((run) => run && run.session_id === binding.session &&
    run.metadata?.project_id === binding.project);
  const visible = target ? (targetMatches && targetRun ? [targetRun] : []) : associated;
  return <div className="space-y-3 text-sm"><h3 className="font-semibold">当前会话的任务关联成果</h3>
    <p>默认列出当前项目和会话的近期运行及活动运行；指定任务时按 ID 单独读取，包括较早运行。附件来自已记录的工具结果；文本成果需按运行读取登记关联。关联不证明成果由该运行生成，内容均需自行核对。</p>
    <div className="flex flex-wrap items-end gap-2 rounded border border-line p-2 text-xs" aria-label="查找任务关联成果">
      <label className="min-w-0 flex-1">查找成果记录<input aria-label="查找任务成果" className="mt-1 block w-full rounded border border-line bg-surface-0 p-1" value={deliveryQuery} maxLength={200} onChange={(event) => setDeliveryQuery(event.target.value)} placeholder="文件名、路径或记录 ID" /></label>
      <label>成果类型<select aria-label="筛选任务成果类型" className="mt-1 block rounded border border-line bg-surface-0 p-1" value={deliveryKind} onChange={(event) => setDeliveryKind(event.target.value as typeof deliveryKind)}>
        <option value="all">全部</option><option value="attachments">关联附件</option><option value="text">登记文本成果</option>
      </select></label>
      {(deliveryQuery || deliveryKind !== "all") && <Button type="button" size="sm" variant="ghost" onClick={() => { setDeliveryQuery(""); setDeliveryKind("all"); }}>清除筛选</Button>}
      <p className="w-full text-ink-2">仅筛选当前显示运行中已记录的附件名称/ID，以及手动读取后已加载的文本路径/ID；不搜索内容或未加载页面。筛选不清除已选预览或登记草稿。</p>
    </div>
    {binding.session && (target && targetMatches ? <Button type="button" size="sm" variant="secondary" disabled={targetBusy} onClick={() => void loadTarget()}>重新读取此任务</Button> :
      <Button type="button" size="sm" variant="secondary" disabled={loading} onClick={() => void load(binding.session)}>刷新运行</Button>)}
    {target && <div><p>{targetMatches ? `正在查看任务 ${target.run}` : "目标任务的项目、会话或服务已变化，请重新选择。"}</p><Button type="button" size="sm" variant="ghost" onClick={onClearTarget}>查看全部近期运行</Button></div>}
    {!binding.session ? <p>请先选择会话。</p> : target && !targetMatches ? null : target && targetBusy ? <p role="status">正在读取并核对目标任务…</p> : target && targetError ? <p role="alert">无法读取目标任务：{targetError}</p> : !target && error ? <p role="alert">无法读取运行记录：{error}</p> : !target && loading ? <p role="status">正在读取运行记录…</p> : !visible.length ? <p>{target ? "目标任务尚未确认，请重新读取。" : "当前会话没有可确认项目归属的近期运行。"}</p> : visible.map((run) => <article key={run.id} className="rounded border border-line p-3">
      <p>{run.title || "未命名任务"} · {run.id} · {run.status}</p>
      <div hidden={deliveryKind === "text"}>
        {!run.steps.some((step) => step.status === "completed" && step.run_id === run.id && step.session_id === binding.session &&
          step.payload?.source === "run_attachment_v1" && step.payload.project_id === binding.project && step.payload.service_id === binding.service &&
          typeof step.payload.attachment_id === "string" && typeof step.payload.sha256 === "string") &&
          <p className="text-xs text-ink-2">没有可确认的任务关联附件；未关联的项目成果不在这里显示。</p>}
        <RunAttachments run={run} steps={run.steps} reviewScope={{ projectId: binding.project, sessionId: binding.session!, serviceId: binding.service }} verifyRun={!!target} search={deliveryQuery} transferPending={attachmentTransferPending} onPrepareAttachment={(attachment, runId) => onPrepareAttachment({ attachment, runId })} />
      </div>
      <div hidden={deliveryKind === "attachments"}><details className="mt-2"><summary>登记关联的文本成果</summary><RunTextArtifacts runId={run.id} sessionId={binding.session!} projectId={binding.project} serviceId={binding.service} review={!!target} search={deliveryQuery} onPrepareReport={onPrepareReport} /></details></div>
      <FollowupRunDraft run={run} />
    </article>)}
  </div>;
}
