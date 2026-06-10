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
      <header className="flex flex-col gap-3 border-b border-minimax-border px-6 py-4">
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
        <nav className="flex flex-wrap gap-1 rounded-md border border-minimax-border bg-minimax-panel p-1">
          <TabButton id="models" current={tab} onClick={setTab} icon={<Cpu size={12} />} label="Models" testId="settings-tab-models" />
          <TabButton id="providers" current={tab} onClick={setTab} icon={<Globe size={12} />} label="Providers" testId="settings-tab-providers" />
          <TabButton id="permissions" current={tab} onClick={setTab} icon={<ShieldAlert size={12} />} label="Permissions" testId="settings-tab-permissions" />
          <TabButton id="scheduled" current={tab} onClick={setTab} icon={<CalendarClock size={12} />} label="Scheduled" testId="settings-tab-scheduled" />
          <TabButton id="api-key" current={tab} onClick={setTab} icon={<KeyRound size={12} />} label="API Key" testId="settings-tab-api-key" />
          <TabButton id="agents" current={tab} onClick={setTab} icon={<Bot size={12} />} label="Agents" testId="settings-tab-agents" />
          <TabButton id="teams" current={tab} onClick={setTab} icon={<Users size={12} />} label="Teams" testId="settings-tab-teams" />
          <TabButton id="audit" current={tab} onClick={setTab} icon={<ScrollText size={12} />} label="Audit" testId="settings-tab-audit" />
          <TabButton id="webhooks" current={tab} onClick={setTab} icon={<Webhook size={12} />} label="Webhooks" testId="settings-tab-webhooks" />
          <TabButton id="workflows" current={tab} onClick={setTab} icon={<Workflow size={12} />} label="Workflows" testId="settings-tab-workflows" />
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-5">
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
        "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors " +
        (active ? "bg-minimax-accent/20 text-minimax-accent" : "text-minimax-muted hover:text-minimax-fg")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
