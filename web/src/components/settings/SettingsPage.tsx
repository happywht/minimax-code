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
  CalendarClock,
  Cpu,
  Globe,
  KeyRound,
  ScrollText,
  ShieldAlert,
  Users,
  Webhook,
  Workflow,
  X,
} from "lucide-react";
import { IconButton } from "../../ui";
import { ModelsTab } from "./ModelsTab";
import { ProvidersTab } from "./ProvidersTab";
import { PermissionsTab } from "./PermissionsTab";
import { ScheduledTab } from "./ScheduledTab";
import { ApiKeyTab } from "./ApiKeyTab";
import { AgentsTab } from "./AgentsTab";
import { TeamsTab } from "./TeamsTab";
import { AuditTab } from "./AuditTab";
import { WebhooksTab } from "./WebhooksTab";
import { WorkflowsTab } from "./WorkflowsTab";
import { McpServersTab } from "./McpServersTab";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { ConfirmationDialog } from "../modals/ConfirmationDialog";

export type SettingsTab = "models" | "providers" | "permissions" | "scheduled" | "api-key" | "agents" | "teams" | "audit" | "webhooks" | "workflows" | "mcp-servers";

const TAB_GROUPS: Array<{
  label: string;
  tabs: Array<{ id: SettingsTab; icon: JSX.Element; label: string; testId: string }>;
}> = [
  {
    label: "Core",
    tabs: [
      { id: "models", icon: <Cpu size={12} />, label: "Models", testId: "settings-tab-models" },
      { id: "providers", icon: <Globe size={12} />, label: "Providers", testId: "settings-tab-providers" },
      { id: "api-key", icon: <KeyRound size={12} />, label: "API Key", testId: "settings-tab-api-key" },
      { id: "permissions", icon: <ShieldAlert size={12} />, label: "Permissions", testId: "settings-tab-permissions" },
      { id: "mcp-servers", icon: <Blocks size={12} />, label: "MCP Servers", testId: "settings-tab-mcp-servers" },
    ],
  },
  {
    label: "Automation",
    tabs: [
      { id: "scheduled", icon: <CalendarClock size={12} />, label: "Scheduled", testId: "settings-tab-scheduled" },
      { id: "workflows", icon: <Workflow size={12} />, label: "Workflows", testId: "settings-tab-workflows" },
      { id: "webhooks", icon: <Webhook size={12} />, label: "Webhooks", testId: "settings-tab-webhooks" },
    ],
  },
  {
    label: "Agents",
    tabs: [
      { id: "agents", icon: <Bot size={12} />, label: "Agents", testId: "settings-tab-agents" },
      { id: "teams", icon: <Users size={12} />, label: "Teams", testId: "settings-tab-teams" },
    ],
  },
  {
    label: "Governance",
    tabs: [
      { id: "audit", icon: <ScrollText size={12} />, label: "Audit", testId: "settings-tab-audit" },
    ],
  },
];

export interface SettingsPageProps {
  testId?: string;
  onClose?: () => void;
  initialTab?: SettingsTab;
}

export function SettingsPage({ testId = "settings-page", onClose, initialTab = "models" }: SettingsPageProps): JSX.Element {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const dialogRef = useRef<HTMLDivElement>(null);
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
              Settings
            </h1>
            <p className="text-pretty text-[11px] text-ink-2">
              Configure models, providers, permissions, scheduled jobs, and API keys.
            </p>
          </div>
          {onClose && (
            <IconButton
              data-testid="settings-close"
              aria-label="Close settings"
              onClick={onClose}
            >
              <X />
            </IconButton>
          )}
        </div>
      </header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden md:flex-row">
        <nav
          data-testid="settings-nav"
          className="flex max-w-full shrink-0 gap-2 overflow-x-auto overscroll-x-contain border-b border-line bg-surface-1 px-2 py-2 sm:px-3 md:w-52 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r md:px-3 md:py-4"
          aria-label="Settings sections"
        >
          {TAB_GROUPS.map((group) => (
            <div key={group.label} className="flex shrink-0 gap-1 md:flex-col">
              <div className="hidden px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-ink-2 md:block">
                {group.label}
              </div>
              <div className="flex gap-1 md:flex-col">
                {group.tabs.map((item) => (
                  <TabButton
                    key={item.id}
                    id={item.id}
                    current={tab}
                    onClick={setTab}
                    icon={item.icon}
                    label={item.label}
                    testId={item.testId}
                  />
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-3 py-4 sm:px-4 md:px-6 md:py-5">
          {tab === "models" && <ModelsTab />}
          {tab === "providers" && <ProvidersTab />}
          {tab === "permissions" && <PermissionsTab />}
          {tab === "scheduled" && <ScheduledTab />}
          {tab === "api-key" && <ApiKeyTab />}
          {tab === "agents" && <AgentsTab />}
          {tab === "teams" && <TeamsTab />}
          {tab === "audit" && <AuditTab />}
          {tab === "webhooks" && <WebhooksTab />}
          {tab === "workflows" && <WorkflowsTab />}
          {tab === "mcp-servers" && <McpServersTab />}
        </div>
      </div>
      </div>
      <ConfirmationDialog />
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
