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
  advanced?: boolean;
  tabs: Array<{ id: SettingsTab; icon: JSX.Element; label: string; testId: string }>;
}> = [
  { label: "日常设置", tabs: [
    { id: "models", icon: <Cpu size={12} />, label: strings.settings.page.tabModels, testId: "settings-tab-models" },
    { id: "providers", icon: <Globe size={12} />, label: strings.settings.page.tabProviders, testId: "settings-tab-providers" },
    { id: "hosts", icon: <Server size={12} />, label: "远程主机", testId: "settings-tab-hosts" },
    { id: "permissions", icon: <ShieldAlert size={12} />, label: strings.settings.page.tabPermissions, testId: "settings-tab-permissions" },
    { id: "data", icon: <Database size={12} />, label: "数据与诊断", testId: "settings-tab-data" },
  ] },
  { label: "扩展与工作区", advanced: true, tabs: [
    { id: "mcp-servers", icon: <Blocks size={12} />, label: strings.settings.page.tabMcp, testId: "settings-tab-mcp-servers" },
    { id: "memory", icon: <Brain size={12} />, label: strings.settings.page.tabMemory, testId: "settings-tab-memory" },
    { id: "plugins", icon: <Puzzle size={12} />, label: strings.settings.page.tabPlugins, testId: "settings-tab-plugins" },
    { id: "worktrees", icon: <GitBranch size={12} />, label: strings.settings.page.tabWorktrees, testId: "settings-tab-worktrees" },
  ] },
  { label: "自动化", advanced: true, tabs: [
    { id: "scheduled", icon: <CalendarClock size={12} />, label: strings.settings.page.tabScheduled, testId: "settings-tab-scheduled" },
    { id: "workflows", icon: <Workflow size={12} />, label: strings.settings.page.tabWorkflows, testId: "settings-tab-workflows" },
    { id: "webhooks", icon: <Webhook size={12} />, label: strings.settings.page.tabWebhooks, testId: "settings-tab-webhooks" },
  ] },
  { label: "Agent 与审计", advanced: true, tabs: [
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
  const [advanced, setAdvanced] = useState(() => TAB_GROUPS.some((group) => group.advanced && group.tabs.some((item) => item.id === initialTab)));
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set(
    TAB_GROUPS.filter((group) => group.advanced && !group.tabs.some((item) => item.id === initialTab)).map((group) => group.label),
  ));
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
              模型、提供商、主机和权限优先展示；诊断与备份在“数据与诊断”。切换页面保留草稿，离开或切换服务会丢失未保存内容。
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
      <div className="border-b border-line px-4 py-2 text-xs text-ink-2" role="status">
        当前服务地址：{scope?.host ?? ipc.agentBaseUrl} · 服务身份：{ready ? scope?.service : "待确认"}。模型、凭据和权限属于当前服务，项目相关配置以对应页面说明为准。
        {scopeNotice && <p className="text-status-warning">{scopeNotice}</p>}
        {!ready && <p className="text-status-warning">{scopeError || "正在确认服务身份…"} {scopeError && <button type="button" className="underline" onClick={() => void confirmScope()}>重新确认</button>}</p>}
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden md:flex-row">
        <nav
          data-testid="settings-nav"
          className="flex max-w-full shrink-0 gap-2 overflow-x-auto overscroll-x-contain border-b border-line bg-surface-1 px-2 py-2 sm:px-3 md:w-52 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r md:px-3 md:py-4"
          aria-label={strings.settings.page.sections}
        >
          <button type="button" aria-expanded={advanced} onClick={() => {
            if (advanced) setCollapsedGroups((previous) => new Set([...previous, ...TAB_GROUPS.filter((group) => group.advanced).map((group) => group.label)]));
            setAdvanced((value) => !value);
          }} className="shrink-0 rounded-md px-2 py-1.5 text-left text-xs text-ink-1 hover:bg-surface-2 md:order-2">{advanced ? "收起进阶设置" : "展开进阶设置"}</button>
          {TAB_GROUPS.filter((group) => !group.advanced || advanced || group.tabs.some((item) => item.id === tab)).map((group) => (
            <div key={group.label} className="flex shrink-0 flex-col gap-1">
              <button type="button" aria-expanded={!collapsedGroups.has(group.label)} onClick={() => setCollapsedGroups((previous) => {
                const next = new Set(previous);
                if (next.has(group.label)) next.delete(group.label); else next.add(group.label);
                return next;
              })} className="rounded px-2 py-1 text-left text-[11px] font-medium text-ink-2 hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
                {collapsedGroups.has(group.label) ? "▸" : "▾"} {group.label}
              </button>
              <div className="flex gap-1 md:flex-col">
                {group.tabs.filter((item) => !collapsedGroups.has(group.label) || item.id === tab).map((item) => (
                  <TabButton
                    key={item.id}
                    id={item.id}
                    current={tab}
                    onClick={selectTab}
                    icon={item.icon}
                    label={item.label}
                    testId={item.testId}
                  />
                ))}
              </div>
            </div>
          ))}
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
        "flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-left text-xs transition-colors duration-150 md:w-full " +
        (active
          ? "bg-accent-subtle font-medium text-accent"
          : "text-ink-1 hover:bg-surface-2 hover:text-ink-0")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
