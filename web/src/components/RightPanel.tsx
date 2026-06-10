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
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { ProgressPanel } from "./ProgressPanel";
import { PatchPreviewPanel } from "./PatchPreviewPanel";
import { RunTimelinePanel } from "./RunTimelinePanel";
import { SubAgentPanel } from "./SubAgentPanel";
import { CodeReviewPanel } from "./CodeReviewPanel";
import { TeamRunPanel } from "./TeamRunPanel";
import { typedIPC } from "../ipc";
import { useTaskStore } from "../stores";
import type { AgentInfo } from "../types/ipc";

export interface RightPanelProps {
  testId?: string;
  /** When true, render the panel pre-collapsed. Mainly for tests. */
  defaultCollapsed?: boolean;
  /** Override agent list for tests. */
  initialAgents?: AgentInfo[];
  /** Override the IPC listAgents call for tests. */
  loadAgents?: () => Promise<AgentInfo[]>;
}

export function RightPanel({
  testId = "right-panel",
  defaultCollapsed = false,
  initialAgents,
  loadAgents,
}: RightPanelProps): JSX.Element {
  const [collapsed, setCollapsed] = useState<boolean>(defaultCollapsed);
  const [timelineOpen, setTimelineOpen] = useState<boolean>(true);
  const [patchOpen, setPatchOpen] = useState<boolean>(true);
  const [progressOpen, setProgressOpen] = useState<boolean>(true);
  const [teamOpen, setTeamOpen] = useState<boolean>(true);
  const [subOpen, setSubOpen] = useState<boolean>(true);
  const [reviewOpen, setReviewOpen] = useState<boolean>(false);
  const [teamRunOpen, setTeamRunOpen] = useState<boolean>(true);

  const [agents, setAgents] = useState<AgentInfo[] | null>(initialAgents ?? null);
  const [agentsLoading, setAgentsLoading] = useState<boolean>(!initialAgents);
  const [agentsError, setAgentsError] = useState<string | null>(null);

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

  // Collapsed strip — just the expand button.
  if (collapsed) {
    return (
      <div
        data-testid={`${testId}-collapsed`}
        className="flex w-7 shrink-0 flex-col items-center border-l border-minimax-border bg-minimax-panel transition-all duration-200"
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
      className="flex w-64 shrink-0 flex-col border-l border-minimax-border bg-minimax-panel transition-all duration-200 xl:w-[280px]"
    >
      <header className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-minimax-muted">
          Workspace
        </span>
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

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Section
          testId={`${testId}-timeline`}
          title="Run Timeline"
          open={timelineOpen}
          onToggle={() => setTimelineOpen((v) => !v)}
        >
          <RunTimelinePanel testId={`${testId}-timeline-panel`} />
        </Section>

        <Section
          testId={`${testId}-patch`}
          title="Diff Preview"
          open={patchOpen}
          onToggle={() => setPatchOpen((v) => !v)}
        >
          <PatchPreviewPanel testId={`${testId}-patch-panel`} />
        </Section>

        <Section
          testId={`${testId}-progress`}
          title="进度"
          open={progressOpen}
          onToggle={() => setProgressOpen((v) => !v)}
        >
          <ProgressPanel testId={`${testId}-progress-panel`} />
        </Section>

        <Section
          testId={`${testId}-team`}
          title="Agent Team"
          open={teamOpen}
          onToggle={() => setTeamOpen((v) => !v)}
          actions={
            <button
              type="button"
              aria-label="Refresh agent list"
              data-testid={`${testId}-team-refresh`}
              onClick={(e) => {
                e.stopPropagation();
                void fetchAgents();
              }}
              className="flex h-5 w-5 items-center justify-center rounded text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            >
              <RefreshCw size={10} className={agentsLoading ? "animate-spin" : ""} />
            </button>
          }
        >
          <AgentTeamList
            agents={agents}
            loading={agentsLoading}
            error={agentsError}
            testId={`${testId}-team`}
          />
        </Section>

        <Section
          testId={`${testId}-sub`}
          title="Sub-agents"
          open={subOpen}
          onToggle={() => setSubOpen((v) => !v)}
        >
          <SubAgentPanel testId={`${testId}-sub-panel`} />
        </Section>

        <Section
          testId={`${testId}-review`}
          title="Code Review"
          open={reviewOpen}
          onToggle={() => setReviewOpen((v) => !v)}
        >
          <CodeReviewPanel testId={`${testId}-review-panel`} />
        </Section>

        <Section
          testId={`${testId}-teamrun`}
          title="Team Runs"
          open={teamRunOpen}
          onToggle={() => setTeamRunOpen((v) => !v)}
        >
          <TeamRunPanel />
        </Section>
      </div>
    </aside>
  );
}

function Section({
  testId,
  title,
  open,
  onToggle,
  actions,
  children,
}: {
  testId: string;
  title: string;
  open: boolean;
  onToggle: () => void;
  actions?: React.ReactNode;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section
      data-testid={testId}
      className="border-b border-minimax-border"
    >
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onToggle(); }}
        data-testid={`${testId}-header`}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-left hover:bg-minimax-border/40"
      >
        <span className="text-[11px] font-medium uppercase tracking-wider text-minimax-muted">
          {title}
        </span>
        <span className="flex items-center gap-1.5">
          {actions}
          {open ? <ChevronDown size={12} className="text-minimax-muted" /> : <ChevronRight size={12} className="text-minimax-muted" />}
        </span>
      </div>
      {open && <div data-testid={`${testId}-body`}>{children}</div>}
    </section>
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
