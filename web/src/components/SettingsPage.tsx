/**
 * Settings page — tabbed overlay for the user-facing configuration.
 *
 * Shell only: each tab is a self-contained component imported from `./settings/`.
 * See individual tab files for store hooks, sub-components, and IPC bindings.
 */
import { useEffect, useState } from "react";
import {
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
import {
  ModelsTab,
  ProvidersTab,
  PermissionsTab,
  ScheduledTab,
  ApiKeyTab,
  AgentsTab,
  TeamsTab,
  AuditTab,
  WebhooksTab,
  WorkflowsTab,
} from "./settings";

type Tab = "models" | "providers" | "permissions" | "scheduled" | "api-key" | "agents" | "teams" | "audit" | "webhooks" | "workflows";

const TAB_GROUPS: Array<{
  label: string;
  tabs: Array<{ id: Tab; icon: JSX.Element; label: string; testId: string }>;
}> = [
  {
    label: "Core",
    tabs: [
      { id: "models", icon: <Cpu size={12} />, label: "Models", testId: "settings-tab-models" },
      { id: "providers", icon: <Globe size={12} />, label: "Providers", testId: "settings-tab-providers" },
      { id: "api-key", icon: <KeyRound size={12} />, label: "API Key", testId: "settings-tab-api-key" },
      { id: "permissions", icon: <ShieldAlert size={12} />, label: "Permissions", testId: "settings-tab-permissions" },
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
}

export function SettingsPage({ testId = "settings-page", onClose }: SettingsPageProps): JSX.Element {
  const [tab, setTab] = useState<Tab>("models");

  useEffect(() => {
    if (!onClose) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      data-testid={testId}
      className="flex h-full w-full flex-col overflow-hidden bg-minimax-bg text-minimax-fg"
    >
      <header className="border-b border-minimax-border px-4 py-3 md:px-6 md:py-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 data-testid="settings-title" className="text-base font-semibold">
              Settings
            </h1>
            <p className="text-[11px] text-minimax-muted">
              Configure models, providers, permissions, scheduled jobs, and API keys.
            </p>
          </div>
          {onClose && (
            <button
              type="button"
              data-testid="settings-close"
              aria-label="Close settings"
              onClick={onClose}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          data-testid="settings-nav"
          className="flex shrink-0 gap-2 overflow-x-auto border-b border-minimax-border bg-minimax-panel/50 px-3 py-2 md:w-52 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r md:px-3 md:py-4"
          aria-label="Settings sections"
        >
          {TAB_GROUPS.map((group) => (
            <div key={group.label} className="flex shrink-0 gap-1 md:flex-col">
              <div className="hidden px-2 pb-1 text-[10px] font-medium uppercase tracking-wider text-minimax-muted md:block">
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
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5">
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
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────── Tab chrome ─────────────────────── */

function TabButton({
  id, current, onClick, icon, label, testId,
}: {
  id: Tab; current: Tab; onClick: (t: Tab) => void;
  icon: JSX.Element; label: string; testId: string;
}): JSX.Element {
  const active = id === current;
  return (
    <button
      type="button" role="tab" aria-selected={active}
      data-testid={testId}
      onClick={() => onClick(id)}
      className={
        "flex items-center gap-1.5 whitespace-nowrap rounded px-2.5 py-1.5 text-left text-xs transition-colors md:w-full " +
        (active ? "bg-minimax-accent/15 text-minimax-accent" : "text-minimax-muted hover:bg-minimax-border/60 hover:text-minimax-fg")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
