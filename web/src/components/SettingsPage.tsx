/**
 * Settings page — three-tab overlay for the user-facing configuration
 * of the agent:
 *
 *   - Models:        list / select via `model.*` IPC
 *   - Permissions:   list / upsert / remove via `permission.*` IPC
 *   - Scheduled:     list / enable / disable / delete via `schedule.*` IPC
 *
 * The component is mounted by `App.tsx` when the sidebar nav is
 * "settings" and is otherwise hidden.
 */
import { useEffect, useState } from "react";
import {
  CalendarClock,
  Check,
  Cpu,
  Plus,
  Save,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import {
  useModelStore,
  usePermissionStore,
  useScheduleStore,
} from "../stores";
import { toast } from "./ErrorBoundary";
import type { PermissionRule, ScheduledJob } from "../types/ipc";

type Tab = "models" | "permissions" | "scheduled";

export interface SettingsPageProps {
  testId?: string;
}

export function SettingsPage({ testId = "settings-page" }: SettingsPageProps): JSX.Element {
  const [tab, setTab] = useState<Tab>("models");
  return (
    <div
      data-testid={testId}
      className="flex h-full w-full flex-col overflow-hidden bg-minimax-bg text-minimax-fg"
    >
      <header className="flex items-center justify-between border-b border-minimax-border px-6 py-4">
        <div>
          <h1 data-testid="settings-title" className="text-base font-semibold">
            Settings
          </h1>
          <p className="text-[11px] text-minimax-muted">
            Configure model, permissions, and scheduled jobs.
          </p>
        </div>
        <nav className="flex gap-1 rounded-md border border-minimax-border bg-minimax-panel p-1">
          <TabButton
            id="models"
            current={tab}
            onClick={setTab}
            icon={<Cpu size={12} />}
            label="Models"
            testId="settings-tab-models"
          />
          <TabButton
            id="permissions"
            current={tab}
            onClick={setTab}
            icon={<ShieldAlert size={12} />}
            label="Permissions"
            testId="settings-tab-permissions"
          />
          <TabButton
            id="scheduled"
            current={tab}
            onClick={setTab}
            icon={<CalendarClock size={12} />}
            label="Scheduled"
            testId="settings-tab-scheduled"
          />
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {tab === "models" && <ModelsTab />}
        {tab === "permissions" && <PermissionsTab />}
        {tab === "scheduled" && <ScheduledTab />}
      </div>
    </div>
  );
}

/* ─────────────────────── Tab chrome ─────────────────────── */

function TabButton({
  id,
  current,
  onClick,
  icon,
  label,
  testId,
}: {
  id: Tab;
  current: Tab;
  onClick: (t: Tab) => void;
  icon: JSX.Element;
  label: string;
  testId: string;
}): JSX.Element {
  const active = id === current;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-testid={testId}
      onClick={() => onClick(id)}
      className={
        "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors " +
        (active
          ? "bg-minimax-accent/20 text-minimax-accent"
          : "text-minimax-muted hover:text-minimax-fg")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

/* ─────────────────────── Models tab ─────────────────────── */

function ModelsTab(): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const loading = useModelStore((s) => s.loading);

  useEffect(() => {
    if (models.length === 0) {
      void refresh();
    }
  }, [models.length, refresh]);

  return (
    <section data-testid="settings-models" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Available models</h2>
        <button
          type="button"
          data-testid="settings-models-refresh"
          onClick={() => void refresh()}
          className="rounded border border-minimax-border bg-minimax-panel px-2 py-0.5 text-[11px] text-minimax-muted hover:text-minimax-fg"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <ul className="space-y-1.5" data-testid="settings-models-list">
        {models.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No models available
          </li>
        )}
        {models.map((m) => {
          const isCurrent = m.id === current;
          return (
            <li
              key={m.id}
              data-testid={`settings-model-${m.id}`}
              className={
                "flex items-center justify-between rounded-md border px-3 py-2 text-sm " +
                (isCurrent
                  ? "border-minimax-accent/40 bg-minimax-accent/5"
                  : "border-minimax-border bg-minimax-panel/40")
              }
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-minimax-fg">{m.name}</span>
                  {isCurrent && (
                    <span
                      data-testid={`settings-model-current-${m.id}`}
                      className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[10px] text-minimax-accent"
                    >
                      current
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-minimax-muted">
                  {m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx
                  {m.supports_tools ? " · tools" : ""}
                </div>
              </div>
              <button
                type="button"
                data-testid={`settings-model-select-${m.id}`}
                onClick={() => {
                  if (!isCurrent) void setCurrent(m.id);
                }}
                disabled={isCurrent}
                className={
                  "ml-3 inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs " +
                  (isCurrent
                    ? "cursor-not-allowed border-minimax-border text-minimax-muted"
                    : "border-minimax-accent/40 text-minimax-accent hover:bg-minimax-accent/10")
                }
              >
                {isCurrent ? <Check size={12} /> : null}
                {isCurrent ? "Selected" : "Use"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ─────────────────────── Permissions tab ─────────────────────── */

function PermissionsTab(): JSX.Element {
  const rules = usePermissionStore((s) => s.rules);
  const refresh = usePermissionStore((s) => s.refresh);
  const upsertRule = usePermissionStore((s) => s.upsertRule);
  const removeRule = usePermissionStore((s) => s.removeRule);
  const [draftTool, setDraftTool] = useState("*");
  const [draftPattern, setDraftPattern] = useState("");
  const [draftDecision, setDraftDecision] = useState<"allow" | "deny" | "ask">("allow");

  useEffect(() => {
    if (rules.length === 0) {
      void refresh();
    }
  }, [rules.length, refresh]);

  return (
    <section data-testid="settings-permissions" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Permission rules</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Patterns are matched against tool call arguments. A rule with
          decision <code>allow</code> skips the confirmation modal;{" "}
          <code>deny</code> blocks the call; <code>ask</code> always prompts.
        </p>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-12 gap-2 text-xs">
          <input
            data-testid="settings-permission-tool"
            value={draftTool}
            onChange={(e) => setDraftTool(e.target.value)}
            placeholder="tool (e.g. bash)"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg"
          />
          <input
            data-testid="settings-permission-pattern"
            value={draftPattern}
            onChange={(e) => setDraftPattern(e.target.value)}
            placeholder="pattern (regex)"
            className="col-span-5 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg"
          />
          <select
            data-testid="settings-permission-decision"
            value={draftDecision}
            onChange={(e) =>
              setDraftDecision(e.target.value as "allow" | "deny" | "ask")
            }
            className="col-span-2 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg"
          >
            <option value="allow">allow</option>
            <option value="deny">deny</option>
            <option value="ask">ask</option>
          </select>
          <button
            type="button"
            data-testid="settings-permission-add"
            disabled={!draftPattern.trim() || !draftTool.trim()}
            onClick={async () => {
              const created = await upsertRule({
                tool: draftTool.trim(),
                pattern: draftPattern.trim(),
                decision: draftDecision,
              });
              if (created) {
                toast.success(
                  "Rule saved",
                  `${draftTool} ${draftPattern} → ${draftDecision}`,
                );
                setDraftPattern("");
              }
            }}
            className="col-span-2 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus size={12} />
            Add
          </button>
        </div>
      </div>

      <ul className="space-y-1.5" data-testid="settings-permissions-list">
        {rules.length === 0 && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No permission rules yet
          </li>
        )}
        {rules.map((r) => (
          <PermissionRuleRow
            key={r.id}
            rule={r}
            onDelete={() => void removeRule(r.id)}
            onUpdate={(decision) =>
              void upsertRule({
                id: r.id,
                tool: r.tool,
                pattern: r.pattern,
                decision,
              })
            }
          />
        ))}
      </ul>
    </section>
  );
}

function PermissionRuleRow({
  rule,
  onDelete,
  onUpdate,
}: {
  rule: PermissionRule;
  onDelete: () => void;
  onUpdate: (decision: "allow" | "deny" | "ask") => void;
}): JSX.Element {
  return (
    <li
      data-testid={`settings-permission-row-${rule.id}`}
      className="flex items-center gap-2 rounded-md border border-minimax-border bg-minimax-panel/40 px-3 py-2 text-sm"
    >
      <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[10px] text-minimax-muted">
        {rule.tool}
      </span>
      <code className="flex-1 truncate font-mono text-xs text-minimax-fg">
        {rule.pattern}
      </code>
      <select
        data-testid={`settings-permission-decision-${rule.id}`}
        value={rule.decision}
        onChange={(e) => onUpdate(e.target.value as "allow" | "deny" | "ask")}
        className="rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-xs text-minimax-fg"
      >
        <option value="allow">allow</option>
        <option value="deny">deny</option>
        <option value="ask">ask</option>
      </select>
      <button
        type="button"
        data-testid={`settings-permission-delete-${rule.id}`}
        onClick={onDelete}
        aria-label="Delete rule"
        className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"
      >
        <Trash2 size={12} />
      </button>
    </li>
  );
}

/* ─────────────────────── Scheduled tab ─────────────────────── */

function ScheduledTab(): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const loading = useScheduleStore((s) => s.loading);

  const [draftName, setDraftName] = useState("");
  const [draftCron, setDraftCron] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");

  useEffect(() => {
    if (jobs.length === 0) {
      void refresh();
    }
  }, [jobs.length, refresh]);

  return (
    <section data-testid="settings-scheduled" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Scheduled jobs</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Cron jobs the agent runs on a schedule. Use 5-field cron
          expressions (e.g. <code>*/5 * * * *</code> = every 5 minutes).
        </p>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-12 gap-2 text-xs">
          <input
            data-testid="settings-job-name"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder="job name"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg"
          />
          <input
            data-testid="settings-job-cron"
            value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)}
            placeholder="cron expr"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 font-mono text-minimax-fg"
          />
          <input
            data-testid="settings-job-prompt"
            value={draftPrompt}
            onChange={(e) => setDraftPrompt(e.target.value)}
            placeholder="prompt payload"
            className="col-span-4 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg"
          />
          <button
            type="button"
            data-testid="settings-job-add"
            disabled={!draftName.trim() || !draftCron.trim()}
            onClick={async () => {
              const job = await create({
                name: draftName.trim(),
                cron: draftCron.trim(),
                prompt: draftPrompt.trim(),
              });
              if (job) {
                toast.success("Job created", job.name);
                setDraftName("");
                setDraftCron("");
                setDraftPrompt("");
              }
            }}
            className="col-span-2 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save size={12} />
            Create
          </button>
        </div>
      </div>

      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No scheduled jobs
          </li>
        )}
        {jobs.map((j) => (
          <ScheduledJobRow
            key={j.id}
            job={j}
            onToggle={(enabled) => void setEnabled(j.id, enabled)}
            onDelete={() => void remove(j.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function ScheduledJobRow({
  job,
  onToggle,
  onDelete,
}: {
  job: ScheduledJob;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
}): JSX.Element {
  return (
    <li
      data-testid={`settings-job-row-${job.id}`}
      className="flex items-center gap-2 rounded-md border border-minimax-border bg-minimax-panel/40 px-3 py-2 text-sm"
    >
      <span className="flex-1 min-w-0">
        <span className="block truncate font-medium text-minimax-fg">
          {job.name}
        </span>
        <span className="block truncate font-mono text-[11px] text-minimax-muted">
          {job.cron} · {job.prompt || "(no prompt)"}
        </span>
      </span>
      <label
        className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-minimax-muted"
        data-testid={`settings-job-toggle-${job.id}`}
      >
        <input
          type="checkbox"
          checked={job.enabled}
          onChange={(e) => onToggle(e.target.checked)}
          className="h-3 w-3 accent-minimax-accent"
        />
        {job.enabled ? "enabled" : "disabled"}
      </label>
      <button
        type="button"
        data-testid={`settings-job-delete-${job.id}`}
        onClick={onDelete}
        aria-label="Delete job"
        className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"
      >
        <Trash2 size={12} />
      </button>
    </li>
  );
}
