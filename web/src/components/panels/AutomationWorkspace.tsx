import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useScheduleStore, useSessionStore, useWebhookStore, useWorkflowStore } from "../../stores";
import { Button } from "../../ui";
import { ScheduledTab } from "../settings/ScheduledTab";
import { WorkflowsTab } from "../settings/WorkflowsTab";
import { WebhooksTab } from "../settings/WebhooksTab";

type AutomationTab = "scheduled" | "workflows" | "webhooks";
const tabs: { id: AutomationTab; label: string }[] = [
  { id: "scheduled", label: "定时任务" }, { id: "workflows", label: "工作流" }, { id: "webhooks", label: "Webhook" },
];
type Binding = { host: string; service: string };

export function AutomationWorkspace({ tab, onSelect, onClose, onOpenSession, onEdit, onScheduledEdit, scheduleTarget }: {
  scheduleTarget?: { jobId: string; occurrenceId?: string; host: string } | null;
  tab: AutomationTab; onSelect: (tab: AutomationTab) => void; onClose: () => void; onOpenSession: () => boolean | void; onEdit: () => void; onScheduledEdit?: () => void;
}): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId);
  const projectName = useSessionStore((s) => s.projects.find((item) => item.id === project)?.name);
  const [binding, setBinding] = useState<Binding | null>(null);
  const bindingRef = useRef<Binding | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [visited, setVisited] = useState<Set<AutomationTab>>(() => new Set([tab]));
  const visitedRef = useRef(visited); visitedRef.current = visited;
  const tabRef = useRef(tab); tabRef.current = tab;
  const epoch = useRef(0);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const invalidate = (clearPending = false) => {
    useScheduleStore.getState().invalidateScope(clearPending);
    useWorkflowStore.getState().invalidateScope();
    useWebhookStore.getState().invalidateScope();
  };
  const establish = useCallback(async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setReady(false); setError("");
    try {
      const result = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl) return;
      if (!result.service_id) throw new Error("未返回服务身份");
      const previous = bindingRef.current;
      const same = previous?.host === host && previous.service === result.service_id;
      if (previous && !same) {
        invalidate(true);
        setVisited(new Set([tabRef.current]));
        setNotice("服务身份已改变；定时任务草稿按服务和项目隔离，其他未保存表单已清除。");
      }
      const next = { host, service: result.service_id };
      bindingRef.current = next; setBinding(next); setReady(true);
      if (same) {
        if (visitedRef.current.has("scheduled")) void useScheduleStore.getState().refresh();
        if (visitedRef.current.has("workflows")) void useWorkflowStore.getState().refresh();
        if (visitedRef.current.has("webhooks")) void useWebhookStore.getState().refresh();
      }
    } catch (reason) {
      if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);
  useEffect(() => {
    invalidate(); void establish();
    const off = ipc.onSideCar((event) => {
      epoch.current++; setReady(false); invalidate();
      if (event.status === "started") void establish();
      else setError("连接中断。提交可能已经被服务接收，重连后请先核对结果，避免重复操作。");
    });
    return () => { epoch.current++; off(); invalidate(); };
  }, [establish]);
  useEffect(() => { setVisited((current) => current.has(tab) ? current : new Set([...current, tab])); }, [tab]);
  useEffect(() => {
    const node = surfaceRef.current;
    if (!node) return;
    if (ready) node.removeAttribute("inert"); else node.setAttribute("inert", "");
  }, [ready, binding]);
  const select = (next: AutomationTab) => { setVisited((current) => new Set([...current, next])); onSelect(next); };
  return <section className="flex h-full min-h-0 flex-col" aria-label="自动化工作区">
    <header className="max-h-[45%] shrink-0 space-y-2 overflow-y-auto border-b border-line p-3">
      <div className="flex items-start justify-between gap-2"><div><h2 className="font-semibold">自动化工作区</h2>
        <p className="break-all text-xs text-ink-2">当前项目：{projectName ?? project ?? "未选择"} · 服务地址：{binding?.host ?? ipc.agentBaseUrl} · 服务身份：{ready ? binding?.service : "待确认"}</p></div>
        <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button></div>
      <nav aria-label="自动化类型" className="flex flex-wrap gap-1">{tabs.map((item) => <Button key={item.id} type="button" size="sm" variant={item.id === tab ? "primary" : "ghost"} aria-current={item.id === tab ? "page" : undefined} onClick={() => select(item.id)}>{item.label}</Button>)}</nav>
      <p className="text-xs text-ink-2">离开工作区会丢失未保存的编辑。待确认的定时创建请求（含提示词）保存在当前标签页，供重试。</p>
      <p className="text-xs text-ink-2">运行可能需要权限或用户输入；服务必须可用，不代表无人值守或远程运行已就绪。</p>
      {notice && <p role="status" className="text-xs text-status-warning">{notice}</p>}
      {!ready && <p role="status" className="text-xs text-status-warning">{error || "正在确认当前服务身份…"}</p>}
      {!!error && <Button type="button" size="sm" variant="secondary" onClick={() => void establish()}>重新确认</Button>}
    </header>
    {binding && <div key={JSON.stringify([binding.host, binding.service])} className="relative min-h-0 flex-1">
      {!ready && <div className="absolute inset-0 z-10 bg-surface-overlay/90" aria-hidden="true" />}
      <div ref={surfaceRef} className="h-full overflow-auto p-3 sm:p-4" aria-hidden={!ready || undefined} onChangeCapture={(event) => {
        if (tab === "scheduled") {
          if (!(event.target instanceof Element) || !event.target.closest("[data-schedule-filter]")) onScheduledEdit?.();
        } else onEdit();
      }}>
        {visited.has("scheduled") && <div hidden={tab !== "scheduled"}><ScheduledTab target={scheduleTarget?.host === binding.host ? scheduleTarget : null} key={JSON.stringify([binding.host, binding.service, project])} active={ready} serviceId={binding.service} draftScope={JSON.stringify([binding.host, binding.service, project])} onOpenSession={onOpenSession} onDraftEdit={onScheduledEdit} /></div>}
        {visited.has("workflows") && <div hidden={tab !== "workflows"}><WorkflowsTab /></div>}
        {visited.has("webhooks") && <div hidden={tab !== "webhooks"}><WebhooksTab /></div>}
      </div>
    </div>}
  </section>;
}
