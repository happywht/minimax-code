/**
 * RightPanel — third column of the three-pane shell.
 *
 * Hosts two collapsible sections stacked top-to-bottom:
 *
 *   1. **Progress** — delegates to ``<ProgressPanel />`` which reads
 *      the taskStore and renders the running-task list with status
 *      dots. The container adds a section header with a chevron and
 *      a "No active tasks" empty state (the inner panel also handles
 *      its own empty state — both are present, only one shows at a
 *      time depending on collapse).
 *
 *   2. **Agent Team** — pulls ``agent.list`` on mount and
 *      whenever the user clicks the refresh chevron, then renders a
 *      card per sub-agent with name + status dot (idle / running /
 *      done) + a short description of the task they're working on
 *      (if any). The status is derived from the taskStore: a running
 *      task whose ``task_id`` matches the agent's id is treated as
 *      the agent's "current task".
 *
 * The whole panel can be collapsed to a thin strip via the chevron
 * pinned to the *left* edge of the column. When collapsed, only the
 * strip and the expand button remain — the sections are hidden.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  GitCompare,
  ListChecks,
  Loader2,
  RefreshCw,
  Rocket,
  TerminalSquare,
  Users,
} from "lucide-react";
import { RunTimelinePanel } from "./RunTimelinePanel";
import { typedIPC } from "../ipc";
import {
  usePatchPreviewStore,
  usePermissionStore,
  useRunnerStore,
  useRunTimelineStore,
  useSubAgentStore,
  useTaskStore,
  useTerminalStore,
} from "../stores";
import type { AgentInfo } from "../types/ipc";

const CodeReviewPanel = lazy(() =>
  import("./CodeReviewPanel").then((module) => ({ default: module.CodeReviewPanel })),
);
const PatchPreviewPanel = lazy(() =>
  import("./PatchPreviewPanel").then((module) => ({ default: module.PatchPreviewPanel })),
);
const ProgressPanel = lazy(() =>
  import("./ProgressPanel").then((module) => ({ default: module.ProgressPanel })),
);
const RunnerPanel = lazy(() =>
  import("./RunnerPanel").then((module) => ({ default: module.RunnerPanel })),
);
const SubAgentPanel = lazy(() =>
  import("./SubAgentPanel").then((module) => ({ default: module.SubAgentPanel })),
);
const TeamRunPanel = lazy(() =>
  import("./TeamRunPanel").then((module) => ({ default: module.TeamRunPanel })),
);
const TerminalPanel = lazy(() =>
  import("./TerminalPanel").then((module) => ({ default: module.TerminalPanel })),
);

export interface RightPanelProps {
  testId?: string;
  /** When true, render the panel pre-collapsed. Mainly for tests. */
  defaultCollapsed?: boolean;
  /** Initial inspector tab. Mainly for tests and future deep links. */
  defaultTab?: InspectorTab;
  /** Override agent list for tests. */
  initialAgents?: AgentInfo[];
  /** Override the IPC listAgents call for tests. */
  loadAgents?: () => Promise<AgentInfo[]>;
}

type InspectorTab =
  | "timeline"
  | "diff"
  | "progress"
  | "agents"
  | "subagents"
  | "review"
  | "teamruns"
  | "terminal"
  | "runner";

const INSPECTOR_TABS: Array<{
  id: InspectorTab;
  label: string;
  icon: JSX.Element;
}> = [
  { id: "timeline", label: "Timeline", icon: <Activity size={12} /> },
  { id: "diff", label: "Diff", icon: <GitCompare size={12} /> },
  { id: "progress", label: "Progress", icon: <ListChecks size={12} /> },
  { id: "agents", label: "Agents", icon: <Bot size={12} /> },
  { id: "subagents", label: "Sub", icon: <Users size={12} /> },
  { id: "review", label: "Review", icon: <CheckCircle2 size={12} /> },
  { id: "teamruns", label: "Runs", icon: <Loader2 size={12} /> },
  { id: "terminal", label: "Term", icon: <TerminalSquare size={12} /> },
  { id: "runner", label: "Run", icon: <Rocket size={12} /> },
];

export function RightPanel({
  testId = "right-panel",
  defaultCollapsed = false,
  defaultTab = "timeline",
  initialAgents,
  loadAgents,
}: RightPanelProps): JSX.Element {
  const [collapsed, setCollapsed] = useState<boolean>(defaultCollapsed);
  const [activeTab, setActiveTab] = useState<InspectorTab>(defaultTab);
  const [followRun, setFollowRun] = useState<boolean>(true);

  const [agents, setAgents] = useState<AgentInfo[] | null>(initialAgents ?? null);
  const [agentsLoading, setAgentsLoading] = useState<boolean>(!initialAgents);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  const timelineSignal = useRunTimelineStore((s) => {
    const stepCount = Object.values(s.runs).reduce((sum, run) => sum + run.steps.length, 0);
    return `${s.order.length}:${stepCount}`;
  });
  const subAgentSignal = useSubAgentStore((s) => {
    const runs = Object.values(s.runs);
    const latest = runs.reduce((max, run) => Math.max(max, run.updated_at), 0);
    return `${runs.length}:${latest}`;
  });
  const taskSignal = useTaskStore((s) => {
    const tasks = Object.values(s.tasks);
    const latest = tasks.reduce((max, task) => Math.max(max, task.updated_at), 0);
    return `${tasks.length}:${latest}`;
  });
  const permissionSignal = usePermissionStore((s) => `${s.pending.length}`);
  const diffSignal = usePatchPreviewStore((s) => `${s.result?.files?.length ?? 0}`);
  const terminalSignal = useTerminalStore((s) => {
    const sessions = Object.values(s.sessions);
    const running = sessions.filter((session) => session.status === "running" || session.status === "starting").length;
    const latest = sessions.reduce((max, session) => Math.max(max, session.updated_at), 0);
    return `${running}:${latest}`;
  });
  const runnerSignal = useRunnerStore((s) => `${s.lastStart?.session.id ?? ""}:${s.starting ? 1 : 0}`);

  const fetchAgents = useCallback(async () => {
    setAgentsLoading(true);
    setAgentsError(null);
    try {
      const loader = loadAgents ?? (() => typedIPC.listAgents().then((r) => r.agents));
      const list = await loader();
      setAgents(list);
    } catch (err) {
      setAgentsError(err instanceof Error ? err.message : String(err));
      setAgents([]);
    } finally {
      setAgentsLoading(false);
    }
  }, [loadAgents]);

  useEffect(() => {
    if (initialAgents) return; // tests supply static data
    void fetchAgents();
  }, [initialAgents, fetchAgents]);

  useEffect(() => {
    if (!followRun || timelineSignal === "0:0") return;
    setActiveTab("timeline");
  }, [followRun, timelineSignal]);

  useEffect(() => {
    if (!followRun || subAgentSignal === "0:0") return;
    setActiveTab("subagents");
  }, [followRun, subAgentSignal]);

  useEffect(() => {
    if (!followRun || taskSignal === "0:0") return;
    setActiveTab("progress");
  }, [followRun, taskSignal]);

  useEffect(() => {
    if (!followRun || permissionSignal === "0") return;
    setActiveTab("timeline");
  }, [followRun, permissionSignal]);

  useEffect(() => {
    if (!followRun || diffSignal === "0") return;
    setActiveTab("diff");
  }, [diffSignal, followRun]);

  useEffect(() => {
    if (!followRun || terminalSignal.startsWith("0:")) return;
    setActiveTab("terminal");
  }, [followRun, terminalSignal]);

  useEffect(() => {
    if (!followRun || runnerSignal === ":0") return;
    setActiveTab("runner");
  }, [followRun, runnerSignal]);

  const selectTab = useCallback((tab: InspectorTab) => {
    setActiveTab(tab);
    setFollowRun(false);
  }, []);

  const resumeFollow = useCallback(() => {
    setFollowRun(true);
  }, []);
  const activeTabMeta = INSPECTOR_TABS.find((tab) => tab.id === activeTab) ?? INSPECTOR_TABS[0];

  // Collapsed strip — just the expand button.
  if (collapsed) {
    return (
      <div
        data-testid={`${testId}-collapsed`}
        className="flex h-full w-7 shrink-0 flex-col items-center border-l border-minimax-border bg-minimax-panel transition-all duration-200"
      >
        <button
          type="button"
          aria-label="Expand right panel"
          data-testid={`${testId}-expand`}
          onClick={() => setCollapsed(false)}
          className="mt-3 flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
        >
          <ChevronLeft size={14} />
        </button>
      </div>
    );
  }

  return (
    <aside
      data-testid={testId}
      className="flex h-full w-64 shrink-0 flex-col border-l border-minimax-border bg-minimax-panel transition-all duration-200 xl:w-[280px]"
    >
      <header className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="min-w-0">
          <span className="block text-[11px] font-medium uppercase tracking-wider text-minimax-muted">
            Inspector
          </span>
          <span className="flex items-center gap-1.5 truncate text-[11px] text-minimax-muted/80">
            <span className="text-minimax-accent">{activeTabMeta.icon}</span>
            <span className="truncate">{activeTabMeta.label}</span>
          </span>
        </div>
        <button
          type="button"
          aria-label="Collapse right panel"
          data-testid={`${testId}-collapse`}
          onClick={() => setCollapsed(true)}
          className="flex h-6 w-6 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
        >
          <ChevronRight size={12} />
        </button>
      </header>

      <div
        role="tablist"
        aria-label="Inspector panels"
        className="flex gap-1 overflow-x-auto border-b border-minimax-border px-2 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {INSPECTOR_TABS.map((tab) => {
          const selected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${testId}-${tab.id}-panel`}
              data-testid={`${testId}-tab-${tab.id}`}
              onClick={() => selectTab(tab.id)}
              title={tab.label}
              className={[
                "flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-[11px] transition-colors duration-200",
                selected
                  ? "bg-minimax-accent/15 text-minimax-accent"
                  : "text-minimax-muted hover:bg-minimax-border/60 hover:text-minimax-fg",
              ].join(" ")}
            >
              {tab.icon}
              <span className="sr-only">{tab.label}</span>
            </button>
          );
        })}
      </div>

      {!followRun ? (
        <div className="flex items-center justify-between border-b border-minimax-border bg-minimax-bg/45 px-3 py-1.5">
          <span className="truncate text-[11px] text-minimax-muted">Manual tab pinned</span>
          <button
            type="button"
            data-testid={`${testId}-resume-follow`}
            onClick={resumeFollow}
            className="rounded px-2 py-1 text-[11px] font-medium text-minimax-accent transition-colors duration-200 hover:bg-minimax-accent/10"
          >
            Follow run
          </button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <InspectorContent
          activeTab={activeTab}
          agents={agents}
          agentsLoading={agentsLoading}
          agentsError={agentsError}
          fetchAgents={fetchAgents}
          testId={testId}
        />
      </div>
    </aside>
  );
}

function InspectorContent({
  activeTab,
  agents,
  agentsLoading,
  agentsError,
  fetchAgents,
  testId,
}: {
  activeTab: InspectorTab;
  agents: AgentInfo[] | null;
  agentsLoading: boolean;
  agentsError: string | null;
  fetchAgents: () => Promise<void>;
  testId: string;
}): JSX.Element {
  if (activeTab === "timeline") {
    return (
      <section id={`${testId}-timeline-panel`} role="tabpanel" data-testid={`${testId}-timeline-body`}>
        <RunTimelinePanel testId={`${testId}-timeline-panel`} />
      </section>
    );
  }
  if (activeTab === "diff") {
    return (
      <section id={`${testId}-diff-panel`} role="tabpanel" data-testid={`${testId}-patch-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <PatchPreviewPanel testId={`${testId}-patch-panel`} />
        </Suspense>
      </section>
    );
  }
  if (activeTab === "progress") {
    return (
      <section id={`${testId}-progress-panel`} role="tabpanel" data-testid={`${testId}-progress-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <ProgressPanel testId={`${testId}-progress-panel`} />
        </Suspense>
      </section>
    );
  }
  if (activeTab === "agents") {
    return (
      <section id={`${testId}-agents-panel`} role="tabpanel" data-testid={`${testId}-team-body`}>
        <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-minimax-muted">
            Agent Team
          </span>
          <button
            type="button"
            aria-label="Refresh agent list"
            data-testid={`${testId}-team-refresh`}
            onClick={() => void fetchAgents()}
            className="flex h-6 w-6 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          >
            <RefreshCw size={10} className={agentsLoading ? "animate-spin" : ""} />
          </button>
        </div>
        <AgentTeamList
          agents={agents}
          loading={agentsLoading}
          error={agentsError}
          testId={`${testId}-team`}
        />
      </section>
    );
  }
  if (activeTab === "subagents") {
    return (
      <section id={`${testId}-subagents-panel`} role="tabpanel" data-testid={`${testId}-sub-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <SubAgentPanel testId={`${testId}-sub-panel`} />
        </Suspense>
      </section>
    );
  }
  if (activeTab === "review") {
    return (
      <section id={`${testId}-review-panel`} role="tabpanel" data-testid={`${testId}-review-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <CodeReviewPanel testId={`${testId}-review-panel`} />
        </Suspense>
      </section>
    );
  }
  if (activeTab === "terminal") {
    return (
      <section id={`${testId}-terminal-panel`} role="tabpanel" data-testid={`${testId}-terminal-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <TerminalPanel testId={`${testId}-terminal-panel`} />
        </Suspense>
      </section>
    );
  }
  if (activeTab === "runner") {
    return (
      <section id={`${testId}-runner-panel`} role="tabpanel" data-testid={`${testId}-runner-body`}>
        <Suspense fallback={<InspectorPanelFallback />}>
          <RunnerPanel testId={`${testId}-runner-panel`} />
        </Suspense>
      </section>
    );
  }
  return (
    <section id={`${testId}-teamruns-panel`} role="tabpanel" data-testid={`${testId}-teamrun-body`}>
      <Suspense fallback={<InspectorPanelFallback />}>
        <TeamRunPanel />
      </Suspense>
    </section>
  );
}

function InspectorPanelFallback(): JSX.Element {
  return (
    <div className="flex min-h-24 items-center justify-center" aria-busy="true">
      <Loader2 size={14} className="animate-spin text-minimax-muted" />
    </div>
  );
}

function AgentTeamList({
  agents,
  loading,
  error,
  testId,
}: {
  agents: AgentInfo[] | null;
  loading: boolean;
  error: string | null;
  testId: string;
}): JSX.Element {
  const tasks = useTaskStore((s) => s.tasks);

  const taskByAgent = useMemo(() => {
    // Convention: a task whose ``task_id`` equals an agent's ``id``
    // (or starts with ``<id>::``) is treated as that agent's
    // "current activity". The prefix form lets the backend namespace
    // additional metadata without losing the linkage.
    const map = new Map<string, (typeof tasks)[string]>();
    const agentList = agents ?? [];
    for (const a of agentList) {
      for (const t of Object.values(tasks)) {
        if (t.task_id === a.id || t.task_id.startsWith(`${a.id}::`)) {
          map.set(a.id, t);
          break;
        }
      }
    }
    return map;
  }, [tasks, agents]);

  if (loading && (agents === null || agents.length === 0)) {
    return (
      <div
        data-testid={`${testId}-loading`}
        className="flex items-center gap-1.5 px-3 py-3 text-[11px] text-minimax-muted"
      >
        <Loader2 size={10} className="animate-spin" />
        Loading agents…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid={`${testId}-error`}
        className="px-3 py-2 text-[11px] text-status-error"
        title={error}
      >
        Failed to load agents
      </div>
    );
  }

  if (!agents || agents.length === 0) {
    return (
      <div
        data-testid={`${testId}-empty`}
        className="px-3 py-3 text-center text-[11px] italic text-minimax-muted"
      >
        No sub-agents
      </div>
    );
  }

  return (
    <ul data-testid={`${testId}-list`} className="space-y-1.5 px-3 pb-3">
      {agents.map((a) => {
        const t = taskByAgent.get(a.id);
        const status: "running" | "done" | "idle" = t
          ? t.status === "running"
            ? "running"
            : t.status === "done"
              ? "done"
              : "idle"
          : "idle";
        return (
          <li
            key={a.id}
            data-testid={`${testId}-card-${a.id}`}
            className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
          >
            <div className="flex items-center gap-2">
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-minimax-accent/20 text-minimax-accent"
                aria-hidden="true"
              >
                <Bot size={10} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span
                    data-testid={`${testId}-card-${a.id}-name`}
                    className="truncate text-[11px] font-medium text-minimax-fg"
                  >
                    {a.name}
                  </span>
                  <AgentStatusDot status={status} />
                </div>
                {t ? (
                  <div
                    data-testid={`${testId}-card-${a.id}-task`}
                    className="mt-0.5 truncate text-[11px] text-minimax-muted"
                    title={t.message ?? ""}
                  >
                    {t.message || t.task_id}
                  </div>
                ) : (
                  <div className="mt-0.5 truncate text-[11px] italic text-minimax-muted">
                    {a.description || (a.enabled ? "idle" : "disabled")}
                  </div>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function AgentStatusDot({ status }: { status: "idle" | "running" | "done" }): JSX.Element {
  if (status === "running") {
    return (
      <span
        data-testid="agent-status-running"
        className="inline-flex items-center text-status-warning"
        title="running"
      >
        <Loader2 size={9} className="animate-spin" />
      </span>
    );
  }
  if (status === "done") {
    return (
      <span
        data-testid="agent-status-done"
        className="inline-flex items-center text-status-success"
        title="done"
      >
        <CheckCircle2 size={9} />
      </span>
    );
  }
  return (
    <span
      data-testid="agent-status-idle"
      className="block h-1.5 w-1.5 rounded-full bg-minimax-muted"
      title="idle"
    />
  );
}
