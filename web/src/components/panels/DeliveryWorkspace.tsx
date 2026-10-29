import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useArtifactStore, useAttachmentStore, useRunTimelineStore, useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import { Button } from "../../ui";
import { RunAttachments } from "../right-panel/RunAttachments";
import { FollowupRunDraft } from "../right-panel/FollowupRunDraft";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { AttachmentsPanel } from "./AttachmentsPanel";
import { OfficeDeliveryPanel } from "./OfficeDeliveryPanel";
import { ResearchSourcesPanel } from "./ResearchSourcesPanel";

export type DeliveryTab = "artifacts" | "attachments" | "office" | "research" | "run-artifacts";
const tabs: Array<{ id: DeliveryTab; label: string }> = [
  { id: "artifacts", label: "文本成果" }, { id: "attachments", label: "附件与报告" },
  { id: "office", label: "办公表格" }, { id: "research", label: "研究来源" },
  { id: "run-artifacts", label: "当前会话成果" },
];
type Binding = { project: string; session: string | null; host: string; service: string };

export function DeliveryWorkspace({ tab, onSelect, onClose, onEdit }: { tab: DeliveryTab; onSelect: (tab: DeliveryTab) => void; onClose: () => void; onEdit?: () => void }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId), session = useSessionStore((s) => s.currentSessionId);
  const projectName = useSessionStore((s) => s.projects.find((item) => item.id === project)?.name);
  const host = ipc.agentBaseUrl;
  const [binding, setBinding] = useState<Binding | null>(null), bindingRef = useRef<Binding | null>(null);
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
  const scopeKey = validBinding ? JSON.stringify([validBinding.host, validBinding.project, validBinding.session, validBinding.service]) : "";
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
      <p className="text-xs text-ink-2">不同类型保存在各自的项目记录中；仅当前会话的明确关联显示为任务成果。内容和版式需自行核对。标签切换保留草稿；切换项目、会话或服务会清除未保存编辑，请先保存或复制。</p>
      {!ready && <p role="status" className="text-xs text-status-warning">{error || (!project ? "请先选择项目。" : "正在确认当前服务身份…")}</p>}
      {!!error && project && <Button type="button" size="sm" variant="secondary" onClick={() => void refreshScope(true)}>重新确认</Button>}
    </header>
    {validBinding && <div key={scopeKey} className="relative min-h-0 flex-1">
      {!ready && <div className="absolute inset-0 z-10 bg-surface-overlay/90" aria-hidden="true" />}
      <div ref={surfaceRef} className="h-full" aria-hidden={!ready || undefined} onChangeCapture={(event) => {
        const target = event.target as HTMLElement;
        if ((tab === "attachments" || tab === "office") && !target.getAttribute("aria-label")?.startsWith("搜索")) onEdit?.();
      }} onClickCapture={(event) => {
        const button = (event.target as HTMLElement).closest("button");
        const label = button?.textContent ?? "";
        if ((tab === "attachments" || tab === "office") && /添加|删除|新增|插入|移动|转换|恢复/.test(label)) onEdit?.();
      }}>
        {visited.has("artifacts") && <div className="h-full" hidden={tab !== "artifacts"}><ArtifactsPanel embedded onClose={onClose} /></div>}
        {visited.has("attachments") && <div className="h-full" hidden={tab !== "attachments"}><AttachmentsPanel embedded onClose={onClose} /></div>}
        {visited.has("office") && <div className="h-full" hidden={tab !== "office"}><OfficeDeliveryPanel embedded onClose={onClose} onText={() => switchTab("artifacts")} /></div>}
        {visited.has("research") && <div className="h-full" hidden={tab !== "research"}><ResearchSourcesPanel embedded onClose={onClose} onText={() => switchTab("artifacts")} /></div>}
        {visited.has("run-artifacts") && <div className="h-full overflow-auto p-4" hidden={tab !== "run-artifacts"}><SessionDeliveries binding={validBinding} /></div>}
      </div>
    </div>}
  </section>;
}

function SessionDeliveries({ binding }: { binding: Binding }): JSX.Element {
  const runs = useRunTimelineStore((s) => s.runs), order = useRunTimelineStore((s) => s.order);
  const loading = useRunTimelineStore((s) => s.loading), error = useRunTimelineStore((s) => s.error);
  const load = useRunTimelineStore((s) => s.loadForSession);
  useEffect(() => { void load(binding.session); }, [binding.session, load]);
  const associated = order.map((id) => runs[id]).filter((run) => run && run.session_id === binding.session &&
    run.metadata?.project_id === binding.project && run.steps.some((step) => step.run_id === run.id && step.session_id === run.session_id &&
      step.status === "completed" && step.payload?.source === "run_attachment_v1" && step.payload?.project_id === binding.project && step.payload?.service_id === binding.service));
  return <div className="space-y-3 text-sm"><h3 className="font-semibold">当前会话的任务关联成果</h3>
    <p>仅列出最近运行中已持久记录、与当前项目及服务匹配的工具返回附件。旧记录、手工生成或未成功记录关联的成果请到项目附件查看；幂等调用可能复用旧成果，内容未验证。</p>
    {!binding.session ? <p>请先选择会话。</p> : error ? <p role="alert">无法读取运行记录：{error}</p> : loading ? <p role="status">正在读取运行记录…</p> : !associated.length ? <p>当前会话最近运行没有可确认的关联成果。</p> : associated.map((run) => <article key={run.id} className="rounded border border-line p-3">
      <p>{run.title || "未命名任务"} · {run.id} · {run.status}</p><RunAttachments run={run} steps={run.steps} reviewScope={{ projectId: binding.project, sessionId: binding.session!, serviceId: binding.service }} /><FollowupRunDraft run={run} />
    </article>)}
  </div>;
}
