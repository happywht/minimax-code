import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";

type Area = "projects" | "work" | "deliveries" | "automation";
export type WorkbenchView = "chat" | "preview" | "skills" | "agents" | "settings" | "task-dashboard" |
  "artifacts" | "attachments" | "office" | "research" | "run-artifacts" | "scheduled" | "workflows" | "webhooks";
const labels: Record<Area, string> = { projects: "项目与任务", work: "当前工作", deliveries: "交付物", automation: "自动化" };
const pages: Record<WorkbenchView, string> = {
  chat: "当前会话", preview: "工作区预览", skills: "技能", agents: "Agent 管理", settings: "设置",
  "task-dashboard": "任务状态与结果", artifacts: "文本成果", attachments: "附件与报告", office: "办公表格",
  research: "研究来源", "run-artifacts": "当前会话成果", scheduled: "定时任务", workflows: "工作流", webhooks: "Webhook",
};
const areaPages: Record<Area, WorkbenchView[]> = {
  projects: ["task-dashboard"],
  work: ["chat", "preview", "skills", "agents"],
  deliveries: ["run-artifacts", "artifacts", "attachments", "office", "research"],
  automation: ["scheduled", "workflows", "webhooks"],
};
function areaFor(view: WorkbenchView): Area | null {
  return (Object.keys(areaPages) as Area[]).find((area) => areaPages[area].includes(view)) ?? null;
}

export function WorkbenchNavigator({ view, connection, onNavigate }: {
  view: WorkbenchView; connection: "connecting" | "connected" | "disconnected" | "error";
  onNavigate: (view: WorkbenchView) => void;
}): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.projects.find((item) => item.id === projectId));
  const session = useSessionStore((state) => state.sessions.find((item) => item.id === sessionId));
  const last = useRef<Record<Area, WorkbenchView>>({ projects: "task-dashboard", work: "chat", deliveries: "artifacts", automation: "scheduled" });
  const area = areaFor(view);
  const [host, setHost] = useState(ipc.agentBaseUrl);
  useEffect(() => { if (area) last.current[area] = view; }, [area, view]);
  useEffect(() => {
    setHost(ipc.agentBaseUrl);
    return ipc.onSideCar(() => setHost(ipc.agentBaseUrl));
  }, [connection]);
  const association = !sessionId ? "未选择会话" : !session || !session.project_id ? "会话信息待确认" : session.project_id !== projectId ? "会话与当前项目不一致" : null;
  return <div className="shrink-0 border-b border-line bg-surface-1 px-3 py-2 text-xs" data-testid="workbench-navigator">
    <nav aria-label="工作台区域" className="flex flex-wrap gap-1">
      {(Object.keys(labels) as Area[]).map((item) => <button key={item} type="button" aria-current={area === item ? "page" : undefined}
        onClick={() => { if (area !== item) onNavigate(last.current[item]); }}
        className={`rounded-md px-2 py-1 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${area === item ? "bg-accent-subtle text-ink-0" : "text-ink-1 hover:bg-surface-3"}`}>
        {labels[item]}</button>)}
    </nav>
    {area === "work" && <nav aria-label={`${labels[area]}页面`} className="mt-1 flex gap-1 overflow-x-auto whitespace-nowrap">
      {areaPages[area].map((page) => <button key={page} type="button" aria-current={view === page ? "page" : undefined}
        onClick={() => { if (view !== page) onNavigate(page); }}
        className={`rounded px-2 py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${view === page ? "bg-surface-3 text-ink-0" : "text-ink-2 hover:bg-surface-3"}`}>
        {pages[page]}</button>)}
    </nav>}
    <div className="mt-1 flex items-center gap-3 overflow-x-auto whitespace-nowrap text-ink-2">
      <span>位置：{area ? `${labels[area]} / ` : ""}{pages[view]}</span>
      <span className="max-w-48 shrink-0 truncate" title={project?.name ?? projectId ?? "未选择"}>项目：{project?.name ?? projectId ?? "未选择"}</span>
      <span className="max-w-48 shrink-0 truncate" title={session?.title || sessionId || "未选择"}>会话：{session?.title || sessionId || "未选择"}</span>
      <span className="shrink-0">服务地址：{host} · {connection === "connected" ? "已连接" : "连接待确认"}</span>
    </div>
    {association && <p role="status" className="mt-1 text-status-warning">{association}</p>}
    {connection !== "connected" && <p className="mt-1 text-status-warning">项目与会话为最近选择，连接恢复后请重新核对。</p>}
    {area === "automation" && <p className="mt-1 text-ink-2">定时任务使用各自配置的项目；工作流和 Webhook 属于当前服务。</p>}
  </div>;
}
