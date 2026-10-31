/**
 * Settings page — tabbed overlay for the user-facing configuration.
 *
 * Shell only: each tab is a self-contained component imported from `./`.
 * See individual tab files for store hooks, sub-components, and IPC bindings.
 */
import { useEffect, useRef, useState } from "react";
import {
  Blocks,
  Bot,
  Brain,
  CalendarClock,
  Cpu,
  Database,
  GitBranch,
  Globe,
  Puzzle,
  ScrollText,
  Server,
  ShieldAlert,
  Users,
  Webhook,
  Workflow,
  X,
} from "lucide-react";
import { IconButton } from "../../ui";
import { strings } from "../../ui/strings";
import { ModelsTab } from "./ModelsTab";
import { ProvidersTab } from "./ProvidersTab";
import { PermissionsTab } from "./PermissionsTab";
import { ScheduledTab } from "./ScheduledTab";
import { AgentsTab } from "./AgentsTab";
import { TeamsTab } from "./TeamsTab";
import { AuditTab } from "./AuditTab";
import { WebhooksTab } from "./WebhooksTab";
import { WorkflowsTab } from "./WorkflowsTab";
import { McpServersTab } from "./McpServersTab";
import { MemoryTab } from "./MemoryTab";
import { PluginsTab } from "./PluginsTab";
import { DataTab } from "./DataTab";
import { HostsTab } from "./HostsTab";
import { WorktreesTab } from "./WorktreesTab";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { ipc, typedIPC } from "../../ipc";

export type SettingsTab = "hosts" | "models" | "providers" | "permissions" | "scheduled" | "agents" | "teams" | "audit" | "webhooks" | "workflows" | "mcp-servers" | "memory" | "plugins" | "worktrees" | "data";

const TAB_GROUPS: Array<{
  label: string;
  tabs: Array<{ id: SettingsTab; icon: JSX.Element; label: string; testId: string }>;
}> = [
  { label: "基本设置", tabs: [
    { id: "models", icon: <Cpu size={12} />, label: strings.settings.page.tabModels, testId: "settings-tab-models" },
    { id: "providers", icon: <Globe size={12} />, label: strings.settings.page.tabProviders, testId: "settings-tab-providers" },
    { id: "hosts", icon: <Server size={12} />, label: "远程主机", testId: "settings-tab-hosts" },
    { id: "permissions", icon: <ShieldAlert size={12} />, label: strings.settings.page.tabPermissions, testId: "settings-tab-permissions" },
    { id: "data", icon: <Database size={12} />, label: "数据与诊断", testId: "settings-tab-data" },
  ] },
  { label: "扩展与工作区", tabs: [
    { id: "mcp-servers", icon: <Blocks size={12} />, label: strings.settings.page.tabMcp, testId: "settings-tab-mcp-servers" },
    { id: "memory", icon: <Brain size={12} />, label: strings.settings.page.tabMemory, testId: "settings-tab-memory" },
    { id: "plugins", icon: <Puzzle size={12} />, label: strings.settings.page.tabPlugins, testId: "settings-tab-plugins" },
    { id: "worktrees", icon: <GitBranch size={12} />, label: strings.settings.page.tabWorktrees, testId: "settings-tab-worktrees" },
  ] },
  { label: "自动化", tabs: [
    { id: "scheduled", icon: <CalendarClock size={12} />, label: strings.settings.page.tabScheduled, testId: "settings-tab-scheduled" },
    { id: "workflows", icon: <Workflow size={12} />, label: strings.settings.page.tabWorkflows, testId: "settings-tab-workflows" },
    { id: "webhooks", icon: <Webhook size={12} />, label: strings.settings.page.tabWebhooks, testId: "settings-tab-webhooks" },
  ] },
  { label: "Agent 与审计", tabs: [
    { id: "agents", icon: <Bot size={12} />, label: strings.settings.page.tabAgents, testId: "settings-tab-agents" },
    { id: "teams", icon: <Users size={12} />, label: strings.settings.page.tabTeams, testId: "settings-tab-teams" },
    { id: "audit", icon: <ScrollText size={12} />, label: strings.settings.page.tabAudit, testId: "settings-tab-audit" },
  ] },
];

export interface SettingsPageProps {
  testId?: string;
  onClose?: () => void;
  initialTab?: SettingsTab;
  onEdit?: () => void;
}

export function SettingsPage({ testId = "settings-page", onClose, initialTab = "models", onEdit }: SettingsPageProps): JSX.Element {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [visited, setVisited] = useState<Set<SettingsTab>>(() => new Set([initialTab]));
  const [scope, setScope] = useState<{ host: string; service: string } | null>(null);
  const scopeRef = useRef<{ host: string; service: string } | null>(null);
  const tabRef = useRef(tab); tabRef.current = tab;
  const epoch = useRef(0);
  const [ready, setReady] = useState(false);
  const [scopeError, setScopeError] = useState("");
  const [scopeNotice, setScopeNotice] = useState("");
  const surfaceRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmScope = async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setReady(false); setScopeError("");
    try {
      const result = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl) return;
      if (!result.service_id) throw new Error("未返回服务身份");
      const previous = scopeRef.current;
      if (previous && (previous.host !== host || previous.service !== result.service_id)) {
        setVisited(new Set([tabRef.current]));
        setScopeNotice("服务身份已改变，旧服务的未保存表单已清除，请重新输入。");
      }
      const next = { host, service: result.service_id };
      scopeRef.current = next; setScope(next); setReady(true);
    } catch (error) { if (ticket === epoch.current) setScopeError(error instanceof Error ? error.message : String(error)); }
  };
  useEffect(() => {
    void confirmScope();
    const off = ipc.onSideCar((event) => {
      epoch.current++; setReady(false);
      if (event.status === "started") void confirmScope();
      else setScopeError("服务连接中断，设置暂不可操作；已提交的更改请重连后核对。");
    });
    return () => { epoch.current++; off(); };
  }, []); // Connection subscription uses refs to keep the latest scope.
  useEffect(() => {
    if (!surfaceRef.current) return;
    if (ready) surfaceRef.current.removeAttribute("inert"); else surfaceRef.current.setAttribute("inert", "");
  }, [ready, scope]);
  const selectTab = (next: SettingsTab) => { setVisited((current) => new Set([...current, next])); setTab(next); };

  useFocusTrap(dialogRef, Boolean(onClose));

  useEffect(() => {
    if (!onClose) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <>
      <div
        ref={dialogRef}
        data-testid={testId}
        role={onClose ? "dialog" : undefined}
        aria-modal={onClose ? true : undefined}
        aria-labelledby="settings-title"
        className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-surface-0 text-ink-0"
      >
      <header className="border-b border-line px-4 py-3 md:px-6">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 id="settings-title" data-testid="settings-title" className="text-sm font-semibold">
              {strings.settings.page.title}
            </h1>
            <p className="text-pretty text-[11px] text-ink-2">
              切换设置项会保留草稿；离开或切换服务会丢失未保存内容。
            </p>
          </div>
          {onClose && (
            <IconButton
              data-testid="settings-close"
              aria-label={strings.settings.page.close}
              onClick={onClose}
            >
              <X />
            </IconButton>
          )}
        </div>
      </header>
      <div className="border-b border-line/60 px-4 py-2 text-xs text-ink-2 md:px-6" role="status">
        <details>
          <summary className="cursor-pointer">当前服务：{scope?.host ?? ipc.agentBaseUrl} · {ready ? "已确认" : "待确认"}</summary>
          <p className="pt-1">服务身份：{ready ? scope?.service : "待确认"}。模型、凭据和权限属于当前服务，项目相关配置以对应页面说明为准。</p>
        </details>
        {scopeNotice && <p className="text-status-warning">{scopeNotice}</p>}
        {!ready && <p className="text-status-warning">{scopeError || "正在确认服务身份…"} {scopeError && <button type="button" className="underline" onClick={() => void confirmScope()}>重新确认</button>}</p>}
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden md:flex-row">
        <nav
          data-testid="settings-nav"
          className="shrink-0 border-b border-line/60 bg-surface-1 px-4 py-3 md:w-56 md:overflow-y-auto md:border-b-0 md:border-r md:px-3 md:py-4"
          aria-label={strings.settings.page.sections}
        >
          <label className="flex items-center gap-3 text-xs text-ink-2 md:hidden">
            设置分类
            <select aria-label="设置分类" value={tab} onChange={(event) => selectTab(event.target.value as SettingsTab)} className="min-w-0 flex-1 rounded-lg border border-line bg-surface-0 px-3 py-2 text-sm text-ink-0">
              {TAB_GROUPS.map((group) => <optgroup key={group.label} label={group.label}>
                {group.tabs.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </optgroup>)}
            </select>
          </label>
          <div className="hidden space-y-5 md:block">
            {TAB_GROUPS.map((group) => (
              <div key={group.label}>
                <p className="px-2.5 pb-1.5 text-[11px] font-medium text-ink-2">{group.label}</p>
                <div className="space-y-0.5">
                  {group.tabs.map((item) => (
                    <TabButton key={item.id} id={item.id} current={tab} onClick={selectTab} icon={item.icon} label={item.label} testId={item.testId} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </nav>
        {scope && <div key={JSON.stringify([scope.host, scope.service])} className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          {!ready && <div className="absolute inset-0 z-10 bg-surface-overlay/90" aria-hidden="true" />}
          <div ref={surfaceRef} aria-hidden={!ready || undefined} onChangeCapture={onEdit} className="h-full overflow-x-hidden overflow-y-auto px-3 py-4 sm:px-4 md:px-6 md:py-5">
            {visited.has("models") && <div hidden={tab !== "models"}><ModelsTab /></div>}
            {visited.has("providers") && <div hidden={tab !== "providers"}><ProvidersTab /></div>}
            {visited.has("permissions") && <div hidden={tab !== "permissions"}><PermissionsTab /></div>}
            {visited.has("scheduled") && <div hidden={tab !== "scheduled"}><ScheduledTab serviceId={scope.service} active={ready} onOpenSession={onClose} /></div>}
            {visited.has("agents") && <div hidden={tab !== "agents"}><AgentsTab /></div>}
            {visited.has("teams") && <div hidden={tab !== "teams"}><TeamsTab /></div>}
            {visited.has("audit") && <div hidden={tab !== "audit"}><AuditTab /></div>}
            {visited.has("webhooks") && <div hidden={tab !== "webhooks"}><WebhooksTab /></div>}
            {visited.has("workflows") && <div hidden={tab !== "workflows"}><WorkflowsTab /></div>}
            {visited.has("mcp-servers") && <div hidden={tab !== "mcp-servers"}><McpServersTab /></div>}
            {visited.has("memory") && <div hidden={tab !== "memory"}><MemoryTab /></div>}
            {visited.has("plugins") && <div hidden={tab !== "plugins"}><PluginsTab /></div>}
            {visited.has("hosts") && <div hidden={tab !== "hosts"}><HostsTab /></div>}
            {visited.has("worktrees") && <div hidden={tab !== "worktrees"}><WorktreesTab /></div>}
            {visited.has("data") && <div hidden={tab !== "data"}><DataTab /></div>}
          </div>
        </div>}
      </div>
      </div>
    </>
  );
}

/* ─────────────────────── Tab chrome ─────────────────────── */

function TabButton({
  id, current, onClick, icon, label, testId,
}: {
  id: SettingsTab; current: SettingsTab; onClick: (t: SettingsTab) => void;
  icon: JSX.Element; label: string; testId: string;
}): JSX.Element {
  const active = id === current;
  return (
    <button
      type="button" role="tab" aria-selected={active}
      data-testid={testId}
      onClick={() => onClick(id)}
      className={
        "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-left text-xs transition-colors duration-150 md:w-full " +
        (active
          ? "bg-surface-3 font-medium text-ink-0"
          : "text-ink-1 hover:bg-surface-2 hover:text-ink-0")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
