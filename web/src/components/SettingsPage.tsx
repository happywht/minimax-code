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
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  Play,
  Save,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import {
  useAgentStore,
  useModelStore,
  usePermissionStore,
  useScheduleStore,
  useSecretStore,
  useTaskStore,
} from "../stores";
import { toast } from "./ErrorBoundary";
import type { PermissionRule, ScheduledJob } from "../types/ipc";

type Tab = "models" | "permissions" | "scheduled" | "api-key" | "agents";

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
            Configure model, permissions, scheduled jobs, and API key.
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
          <TabButton
            id="api-key"
            current={tab}
            onClick={setTab}
            icon={<KeyRound size={12} />}
            label="API Key"
            testId="settings-tab-api-key"
          />
          <TabButton
            id="agents"
            current={tab}
            onClick={setTab}
            icon={<Bot size={12} />}
            label="Agents"
            testId="settings-tab-agents"
          />
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {tab === "models" && <ModelsTab />}
        {tab === "permissions" && <PermissionsTab />}
        {tab === "scheduled" && <ScheduledTab />}
        {tab === "api-key" && <ApiKeyTab />}
        {tab === "agents" && <AgentsTab />}
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
              await upsertRule({
                tool: draftTool.trim(),
                pattern: draftPattern.trim(),
                decision: draftDecision,
              });
              toast.success(
                "Rule saved",
                `${draftTool} ${draftPattern} → ${draftDecision}`,
              );
              setDraftPattern("");
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
  const runNow = useScheduleStore((s) => s.runNow);
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
            onRunNow={() => void runNow(j.id)}
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
  onRunNow,
}: {
  job: ScheduledJob;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  onRunNow: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tasks = useTaskStore((s) => s.tasks);

  // Find tasks related to this job by matching job name in the task message
  // or task_id prefix. The scheduler creates tasks with labels containing
  // the job name, so a simple substring match works well enough.
  const relatedTasks = useMemo(() => {
    const allTasks = Object.values(tasks);
    if (allTasks.length === 0) return [];
    const jobName = job.name.toLowerCase();
    return allTasks.filter(
      (t) =>
        (t.message && t.message.toLowerCase().includes(jobName)) ||
        t.task_id.toLowerCase().includes(job.id.toLowerCase().slice(0, 6)),
    );
  }, [tasks, job.name, job.id]);

  return (
    <li
      data-testid={`settings-job-row-${job.id}`}
      className="rounded-md border border-minimax-border bg-minimax-panel/40"
    >
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <button
          type="button"
          data-testid={`settings-job-expand-${job.id}`}
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
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
          data-testid={`settings-job-run-now-${job.id}`}
          onClick={onRunNow}
          aria-label="Run job now"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-emerald-300"
        >
          <Play size={12} />
        </button>
        <button
          type="button"
          data-testid={`settings-job-delete-${job.id}`}
          onClick={onDelete}
          aria-label="Delete job"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"
        >
          <Trash2 size={12} />
        </button>
      </div>
      {expanded && (
        <div
          data-testid={`settings-job-tasks-${job.id}`}
          className="border-t border-minimax-border/60 px-3 py-2"
        >
          {relatedTasks.length === 0 ? (
            <div className="text-[11px] italic text-minimax-muted">
              No task runs recorded yet.
            </div>
          ) : (
            <ul className="space-y-1">
              {relatedTasks.map((t) => (
                <li
                  key={t.task_id}
                  className="flex items-center justify-between text-[11px]"
                >
                  <div className="min-w-0 flex items-center gap-1.5">
                    <TaskStatusDot status={t.status} />
                    <span className="truncate text-minimax-fg">
                      {t.message || t.task_id}
                    </span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-minimax-muted">
                    <span>{Math.round(t.progress * 100)}%</span>
                    <span>
                      {t.status === "running"
                        ? "running"
                        : new Date(t.updated_at).toLocaleTimeString()}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function TaskStatusDot({ status }: { status: string }): JSX.Element {
  const colors: Record<string, string> = {
    running: "bg-minimax-accent animate-pulse",
    done: "bg-emerald-400",
    error: "bg-red-400",
    cancelled: "bg-minimax-muted",
  };
  return (
    <span
      aria-hidden
      className={`inline-block h-1.5 w-1.5 rounded-full ${colors[status] ?? "bg-minimax-muted"}`}
    />
  );
}

/* ──────────────────────── API Key tab ──────────────────────── */

function ApiKeyTab(): JSX.Element {
  const status = useSecretStore((s) => s.status);
  const loading = useSecretStore((s) => s.loading);
  const refresh = useSecretStore((s) => s.refresh);
  const setKey = useSecretStore((s) => s.setKey);
  const clear = useSecretStore((s) => s.clear);

  const [draft, setDraft] = useState("");
  const [reveal, setReveal] = useState(false);

  useEffect(() => {
    if (status === null) {
      void refresh();
    }
  }, [status, refresh]);

  const sourceLabel: Record<"keyring" | "env" | "none", string> = {
    keyring: "OS keyring",
    env: "environment variable",
    none: "not configured",
  };

  const statusPill = status
    ? {
        keyring: {
          text: "Stored in OS keyring",
          tone: "bg-minimax-accent/20 text-minimax-accent",
        },
        env: {
          text: "Using environment variable",
          tone: "bg-minimax-border text-minimax-muted",
        },
        none: {
          text: "Not configured — agent in mock mode",
          tone: "bg-red-500/15 text-red-300",
        },
      }[status.source]
    : { text: "Loading…", tone: "bg-minimax-border text-minimax-muted" };

  const hasKey = status?.configured ?? false;

  return (
    <section data-testid="settings-api-key" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">MiniMax API key</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Used to call the MiniMax LLM. Stored in the OS keyring
          (Windows Credential Manager / macOS Keychain / Linux
          Secret Service). Falls back to the
          <code className="mx-1 rounded bg-minimax-panel px-1.5 py-0.5 font-mono text-[10px]">
            MINIMAX_API_KEY
          </code>
          env var if no keyring entry exists.
        </p>
      </div>

      <div
        data-testid="settings-api-key-status"
        className={
          "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs " +
          statusPill.tone
        }
      >
        <KeyRound size={12} />
        <span data-testid="settings-api-key-status-text">{statusPill.text}</span>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <label
          htmlFor="api-key-input"
          className="text-[11px] text-minimax-muted"
        >
          {hasKey
            ? "Replace the keyring entry"
            : "Paste a key to store in the OS keyring"}
        </label>
        <div className="mt-1.5 flex gap-2">
          <div className="relative flex-1">
            <input
              id="api-key-input"
              data-testid="settings-api-key-input"
              type={reveal ? "text" : "password"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  draft.trim() &&
                  !loading
                ) {
                  void (async () => {
                    const ok = await setKey(draft);
                    if (ok) setDraft("");
                  })();
                }
              }}
              placeholder="sk-..."
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 pr-9 font-mono text-xs text-minimax-fg"
            />
            <button
              type="button"
              data-testid="settings-api-key-reveal"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? "Hide API key" : "Show API key"}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-minimax-muted hover:text-minimax-fg"
            >
              {reveal ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          </div>
          <button
            type="button"
            data-testid="settings-api-key-save"
            disabled={!draft.trim() || loading}
            onClick={async () => {
              const ok = await setKey(draft);
              if (ok) {
                setDraft("");
                setReveal(false);
              }
            }}
            className="inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-3 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save size={12} />
            {loading ? "Saving…" : "Save"}
          </button>
        </div>
        <p className="mt-1.5 text-[10px] text-minimax-muted">
          The key is written to <code>{sourceLabel.keyring}</code> on save.
          It is never echoed back through the wire after the write.
        </p>
      </div>

      {status?.source === "keyring" && (
        <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-medium">Keyring entry</h3>
              <p className="mt-0.5 text-[11px] text-minimax-muted">
                Removes the entry from the OS keyring. Does not
                affect the <code>MINIMAX_API_KEY</code> env var.
              </p>
            </div>
            <button
              type="button"
              data-testid="settings-api-key-clear"
              onClick={() => void clear()}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 size={12} />
              Clear keyring
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ─────────────────────── Agents tab ─────────────────────── */

function AgentsTab(): JSX.Element {
  const agents = useAgentStore((s) => s.agents);
  const loading = useAgentStore((s) => s.loading);
  const refresh = useAgentStore((s) => s.refresh);
  const create = useAgentStore((s) => s.create);
  const remove = useAgentStore((s) => s.remove);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formPrompt, setFormPrompt] = useState("");

  useEffect(() => {
    if (agents.length === 0) {
      void refresh();
    }
  }, [agents.length, refresh]);

  const handleCreate = async () => {
    if (!formName.trim() || !formPrompt.trim()) return;
    const a = await create({ name: formName.trim(), system_prompt: formPrompt.trim() });
    if (a) {
      setFormName("");
      setFormPrompt("");
      setShowForm(false);
    }
  };

  return (
    <section data-testid="settings-agents" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">Sub-agents</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Manage agents that can be invoked via <code className="rounded bg-minimax-panel px-1 font-mono text-[10px]">@agent</code> in chat.
          </p>
        </div>
        <button
          type="button"
          data-testid="settings-agent-create"
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20"
        >
          <Plus size={12} />
          New Agent
        </button>
      </div>

      {showForm && (
        <div
          data-testid="settings-agent-form"
          className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3 space-y-2"
        >
          <input
            data-testid="settings-agent-form-name"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder="Agent name (e.g. code-reviewer)"
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg"
          />
          <textarea
            data-testid="settings-agent-form-prompt"
            value={formPrompt}
            onChange={(e) => setFormPrompt(e.target.value)}
            placeholder="System prompt…"
            rows={3}
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg resize-none"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-minimax-fg"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="settings-agent-form-submit"
              onClick={() => void handleCreate()}
              disabled={!formName.trim() || !formPrompt.trim()}
              className="rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Create
            </button>
          </div>
        </div>
      )}

      {loading && agents.length === 0 ? (
        <div className="py-4 text-center text-xs text-minimax-muted">Loading agents…</div>
      ) : agents.length === 0 ? (
        <div className="py-4 text-center text-xs italic text-minimax-muted">
          No sub-agents configured. Click "New Agent" to create one.
        </div>
      ) : (
        <ul className="space-y-2">
          {agents.map((a) => (
            <li
              key={a.id}
              data-testid={`settings-agent-row-${a.name}`}
              className="flex items-center justify-between rounded-md border border-minimax-border bg-minimax-panel/40 p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <Bot size={12} className="text-minimax-accent" />
                  <span className="truncate text-xs font-medium text-minimax-fg">
                    {a.name}
                  </span>
                  {a.enabled ? (
                    <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[9px] text-emerald-300">
                      enabled
                    </span>
                  ) : (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[9px] text-minimax-muted">
                      disabled
                    </span>
                  )}
                </div>
                {a.description && (
                  <p className="mt-0.5 truncate text-[10px] text-minimax-muted">
                    {a.description}
                  </p>
                )}
              </div>
              <button
                type="button"
                data-testid={`settings-agent-delete-${a.name}`}
                onClick={() => void remove(a.name)}
                aria-label={`Delete agent ${a.name}`}
                className="ml-2 rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"
              >
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
