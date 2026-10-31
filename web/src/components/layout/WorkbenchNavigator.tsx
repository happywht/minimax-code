import { useSessionStore } from "../../stores";

type Area = "projects" | "work" | "deliveries" | "automation";
export type WorkbenchView = "chat" | "preview" | "skills" | "agents" | "settings" | "task-dashboard" |
  "artifacts" | "attachments" | "office" | "research" | "run-artifacts" | "scheduled" | "workflows" | "webhooks";
const areaPages: Record<Area, WorkbenchView[]> = {
  projects: ["task-dashboard"], work: ["chat", "preview", "skills", "agents"],
  deliveries: ["run-artifacts", "artifacts", "attachments", "office", "research"], automation: ["scheduled", "workflows", "webhooks"],
};
function areaFor(view: WorkbenchView): Area | null {
  return (Object.keys(areaPages) as Area[]).find((area) => areaPages[area].includes(view)) ?? null;
}

export function WorkbenchNavigator({ view, connection }: {
  view: WorkbenchView; connection: "connecting" | "connected" | "disconnected" | "error";
  onNavigate: (view: WorkbenchView) => void;
}): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const session = useSessionStore((state) => state.sessions.find((item) => item.id === sessionId));
  const area = areaFor(view);
  const association = !sessionId ? null : !session || !session.project_id ? "会话信息待确认" : session.project_id !== projectId ? "会话与当前项目不一致" : null;
  return <div data-testid="workbench-navigator" className={(association && area === "work") || connection !== "connected" ? "shrink-0 px-4 text-xs sm:px-7" : "hidden"}>
    {area === "work" && association && <p role="status" className="text-status-warning">{association}</p>}
    {connection !== "connected" && <p role="status" className="text-status-warning">连接状态待确认；项目与会话为最近选择，连接恢复后请重新核对。</p>}
  </div>;
}
