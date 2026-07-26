import { FormEvent, useEffect, useMemo, useState } from "react";
import { Bot, Loader2, Play, RefreshCw, TerminalSquare } from "lucide-react";
import { useRunnerStore, useSessionStore, useTerminalStore } from "../../stores";
import type {
  RunnerApprovalPolicy,
  RunnerInfo,
  RunnerPermissionMode,
  RunnerSandboxMode,
} from "../../types/ipc";

export interface RunnerPanelProps {
  testId?: string;
}

const RUNNER_SETTINGS_KEY = "minimax-runner-settings";

interface RunnerSettings {
  sandboxMode: RunnerSandboxMode;
  approvalPolicy: RunnerApprovalPolicy;
  permissionMode: RunnerPermissionMode;
}

const DEFAULT_RUNNER_SETTINGS: RunnerSettings = {
  sandboxMode: "workspace-write",
  approvalPolicy: "never",
  permissionMode: "acceptEdits",
};

export function RunnerPanel({ testId = "runner-panel" }: RunnerPanelProps): JSX.Element {
  const runners = useRunnerStore((s) => s.runners);
  const selectedId = useRunnerStore((s) => s.selectedId);
  const loading = useRunnerStore((s) => s.loading);
  const starting = useRunnerStore((s) => s.starting);
  const error = useRunnerStore((s) => s.error);
  const lastStart = useRunnerStore((s) => s.lastStart);
  const setSelected = useRunnerStore((s) => s.setSelected);
  const list = useRunnerStore((s) => s.list);
  const start = useRunnerStore((s) => s.start);
  const adoptTerminal = useTerminalStore((s) => s.adopt);
  const readTerminal = useTerminalStore((s) => s.read);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [settings, setSettings] = useState<RunnerSettings>(readRunnerSettings);

  useEffect(() => {
    void list();
  }, [list]);

  const selected = useMemo(
    () => runners.find((runner) => runner.id === selectedId) ?? runners[0] ?? null,
    [runners, selectedId],
  );
  const canStart = Boolean(selected?.available && command.trim() && !starting);
  const commandPlaceholder = selected?.kind === "external_cli" ? "Ask this runner" : "pnpm test";
  const isCodex = selected?.id === "codex-cli";
  const isClaude = selected?.id === "claude-code-cli";

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void start({
      command,
      cwd: cwd.trim() || undefined,
      session_id: currentSessionId,
      sandbox_mode: isCodex ? settings.sandboxMode : undefined,
      approval_policy: isCodex ? settings.approvalPolicy : undefined,
      permission_mode: isClaude ? settings.permissionMode : undefined,
    }).then((result) => {
      if (!result) return;
      adoptTerminal(result.session);
      void readTerminal(result.session.id);
      setCommand("");
    });
  };

  return (
    <div data-testid={testId} className="flex min-h-0 flex-1 flex-col px-3 pb-3">
      <div className="sticky top-0 z-10 bg-minimax-panel pb-2 pt-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wider text-minimax-muted">
            Runners
          </span>
          <button
            type="button"
            data-testid={`${testId}-refresh`}
            onClick={() => void list()}
            className="flex h-6 w-6 items-center justify-center rounded text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
            aria-label="Refresh runners"
            title="Refresh runners"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
        <div data-testid={`${testId}-list`} className="grid grid-cols-1 gap-1.5">
          {(runners.length ? runners : skeletonRunners()).map((runner) => (
            <RunnerCard
              key={runner.id}
              runner={runner}
              selected={selectedId === runner.id}
              loading={loading && runners.length === 0}
              onClick={() => setSelected(runner.id)}
              testId={`${testId}-runner-${runner.id}`}
            />
          ))}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="border-t border-minimax-border pt-2">
        <div className="flex items-center gap-1.5">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-minimax-border bg-minimax-bg/40 text-minimax-muted">
            <TerminalSquare size={13} />
          </div>
          <input
            data-testid={`${testId}-command`}
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            className="h-8 min-w-0 flex-1 rounded border border-minimax-border bg-minimax-bg px-2 font-mono text-[11px] text-minimax-fg outline-none transition-colors duration-200 placeholder:text-minimax-muted focus:border-minimax-accent/70"
            placeholder={commandPlaceholder}
          />
          <button
            type="submit"
            data-testid={`${testId}-start`}
            disabled={!canStart}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-minimax-accent/15 text-minimax-accent transition-colors duration-200 hover:bg-minimax-accent/25 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Start runner"
            title="Start runner"
          >
            {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          </button>
        </div>
        <input
          data-testid={`${testId}-cwd`}
          value={cwd}
          onChange={(event) => setCwd(event.target.value)}
          className="mt-1 h-7 w-full rounded border border-minimax-border bg-minimax-bg px-2 font-mono text-[11px] text-minimax-muted outline-none transition-colors duration-200 placeholder:text-minimax-muted/70 focus:border-minimax-accent/60"
          placeholder="cwd (default workspace root)"
        />
      </form>

      {selected?.kind === "external_cli" && (
        <div
          data-testid={`${testId}-options`}
          className="mt-2 grid grid-cols-1 gap-1.5 border-t border-minimax-border pt-2"
        >
          {isCodex && (
            <>
              <OptionSelect
                label="Sandbox"
                testId={`${testId}-sandbox`}
                value={settings.sandboxMode}
                options={["workspace-write", "read-only", "danger-full-access"]}
                onChange={(value) =>
                  setSettings((current) => saveRunnerSettings({ ...current, sandboxMode: value }))
                }
              />
              <OptionSelect
                label="Approval"
                testId={`${testId}-approval`}
                value={settings.approvalPolicy}
                options={["never", "on-request", "on-failure", "untrusted"]}
                onChange={(value) =>
                  setSettings((current) =>
                    saveRunnerSettings({ ...current, approvalPolicy: value }),
                  )
                }
              />
            </>
          )}
          {isClaude && (
            <OptionSelect
              label="Permission"
              testId={`${testId}-permission`}
              value={settings.permissionMode}
              options={["acceptEdits", "default", "plan", "bypassPermissions"]}
              onChange={(value) =>
                setSettings((current) => saveRunnerSettings({ ...current, permissionMode: value }))
              }
            />
          )}
        </div>
      )}

      {error && (
        <div
          data-testid={`${testId}-error`}
          className="mt-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-status-error"
          title={error}
        >
          {error}
        </div>
      )}

      {lastStart && (
        <div
          data-testid={`${testId}-last-start`}
          className="mt-2 rounded border border-minimax-border bg-minimax-bg/35 px-2 py-1.5 text-[11px] text-minimax-muted"
        >
          <span className="font-medium text-minimax-fg">{lastStart.runner.label}</span>
          <span className="font-mono"> {lastStart.session.id}</span>
        </div>
      )}
    </div>
  );
}

function readRunnerSettings(): RunnerSettings {
  if (typeof window === "undefined" || !window.localStorage) return DEFAULT_RUNNER_SETTINGS;
  try {
    const raw = window.localStorage.getItem(RUNNER_SETTINGS_KEY);
    if (!raw) return DEFAULT_RUNNER_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<RunnerSettings>;
    return {
      sandboxMode: isRunnerSandboxMode(parsed.sandboxMode)
        ? parsed.sandboxMode
        : DEFAULT_RUNNER_SETTINGS.sandboxMode,
      approvalPolicy: isRunnerApprovalPolicy(parsed.approvalPolicy)
        ? parsed.approvalPolicy
        : DEFAULT_RUNNER_SETTINGS.approvalPolicy,
      permissionMode: isRunnerPermissionMode(parsed.permissionMode)
        ? parsed.permissionMode
        : DEFAULT_RUNNER_SETTINGS.permissionMode,
    };
  } catch {
    return DEFAULT_RUNNER_SETTINGS;
  }
}

function saveRunnerSettings(next: RunnerSettings): RunnerSettings {
  if (typeof window !== "undefined" && window.localStorage) {
    window.localStorage.setItem(RUNNER_SETTINGS_KEY, JSON.stringify(next));
  }
  return next;
}

function isRunnerSandboxMode(value: unknown): value is RunnerSandboxMode {
  return value === "read-only" || value === "workspace-write" || value === "danger-full-access";
}

function isRunnerApprovalPolicy(value: unknown): value is RunnerApprovalPolicy {
  return value === "untrusted" || value === "on-failure" || value === "on-request" || value === "never";
}

function isRunnerPermissionMode(value: unknown): value is RunnerPermissionMode {
  return value === "default" || value === "acceptEdits" || value === "bypassPermissions" || value === "plan";
}

function OptionSelect<T extends string>({
  label,
  testId,
  value,
  options,
  onChange,
}: {
  label: string;
  testId: string;
  value: T;
  options: T[];
  onChange: (value: T) => void;
}): JSX.Element {
  return (
    <label className="flex items-center gap-2 text-[11px] text-minimax-muted">
      <span className="w-14 shrink-0 uppercase tracking-wider">{label}</span>
      <select
        data-testid={testId}
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className="h-7 min-w-0 flex-1 rounded border border-minimax-border bg-minimax-bg px-2 font-mono text-[11px] text-minimax-fg outline-none transition-colors duration-200 focus:border-minimax-accent/60"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function RunnerCard({
  runner,
  selected,
  loading,
  onClick,
  testId,
}: {
  runner: RunnerInfo;
  selected: boolean;
  loading: boolean;
  onClick: () => void;
  testId: string;
}): JSX.Element {
  const enabled = runner.available;
  const tone = runner.available ? "bg-status-success" : "bg-status-error";
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={loading}
      className={[
        "min-w-0 rounded border px-2 py-2 text-left transition-colors duration-200",
        selected
          ? "border-minimax-accent/45 bg-minimax-accent/10"
          : "border-minimax-border bg-minimax-bg/30 hover:border-minimax-accent/30",
      ].join(" ")}
      title={runner.reason ?? runner.command ?? runner.label}
    >
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-minimax-border bg-minimax-panel text-minimax-muted">
          {runner.kind === "native" ? <TerminalSquare size={12} /> : <Bot size={12} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-medium text-minimax-fg">
            {runner.label}
          </span>
          <span className="block truncate font-mono text-[11px] text-minimax-muted">
            {runner.command ?? runner.kind}
          </span>
        </span>
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} />
      </div>
      {!enabled && (
        <div className="mt-1 truncate text-[11px] text-minimax-muted">
          {runner.reason ?? "Adapter pending"}
        </div>
      )}
    </button>
  );
}

function skeletonRunners(): RunnerInfo[] {
  return [
    {
      id: "native",
      label: "Loading",
      kind: "native",
      available: true,
      command: null,
      version: null,
      reason: null,
      supports_prompt: false,
      supports_terminal: true,
    },
  ];
}
