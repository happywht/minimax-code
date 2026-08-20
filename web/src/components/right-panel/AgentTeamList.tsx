/**
 * AgentTeamList — the sub-agent roster rendered inside the Agent
 * Team panel.
 *
 * Each agent gets a card with its name, a semantic status badge
 * (running / done / idle), and a one-line description of its current
 * activity. Activity is derived from the taskStore: a task whose
 * ``task_id`` equals the agent id (or starts with ``<id>::``) is that
 * agent's "current task" — the prefix form lets the backend namespace
 * extra metadata without losing the linkage.
 */
import { useMemo } from "react";
import { Bot, CheckCircle2, Loader2, Users } from "lucide-react";
import { Badge, EmptyState, Spinner } from "../../ui";
import { useTaskStore } from "../../stores";
import type { AgentInfo } from "../../types/ipc";
import { strings } from "../../ui/strings";

type AgentStatus = "idle" | "running" | "done";

export interface AgentTeamListProps {
  agents: AgentInfo[] | null;
  loading: boolean;
  error: string | null;
  testId: string;
}

export function AgentTeamList({
  agents,
  loading,
  error,
  testId,
}: AgentTeamListProps): JSX.Element {
  const tasks = useTaskStore((s) => s.tasks);

  const taskByAgent = useMemo(() => {
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
        className="flex items-center gap-2 px-3 py-3 text-xs text-ink-2"
      >
        <Spinner size={12} />
        {strings.rightPanel.agents.loading}
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid={`${testId}-error`}
        className="px-3 py-2 text-xs text-status-error"
        title={error}
      >
        {strings.rightPanel.agents.loadFailed}
      </div>
    );
  }

  if (!agents || agents.length === 0) {
    return (
      <EmptyState
        testId={`${testId}-empty`}
        icon={<Users />}
        title="暂无子 Agent"
        hint="Agent 运行时装载的子 Agent 会显示在这里。"
      />
    );
  }

  return (
    <ul data-testid={`${testId}-list`} className="space-y-1.5 p-2">
      {agents.map((a) => {
        const t = taskByAgent.get(a.id);
        const status: AgentStatus = t
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
            className="rounded-lg border border-line bg-surface-1 p-2 transition-colors duration-150 hover:border-line-strong"
          >
            <div className="flex items-center gap-2">
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-accent"
                aria-hidden="true"
              >
                <Bot size={12} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-1.5">
                  <span
                    data-testid={`${testId}-card-${a.id}-name`}
                    className="truncate text-xs font-medium text-ink-0"
                  >
                    {a.name}
                  </span>
                  <AgentStatusBadge status={status} />
                </div>
                {t ? (
                  <div
                    data-testid={`${testId}-card-${a.id}-task`}
                    className="mt-0.5 truncate text-[11px] text-ink-2"
                    title={t.message ?? ""}
                  >
                    {t.message || t.task_id}
                  </div>
                ) : (
                  <div className="mt-0.5 truncate text-[11px] italic text-ink-2">
                    {a.description ||
                      (a.enabled
                        ? strings.rightPanel.agents.idle
                        : strings.rightPanel.agents.disabled)}
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

function AgentStatusBadge({ status }: { status: AgentStatus }): JSX.Element {
  if (status === "running") {
    return (
      <Badge
        tone="warning"
        data-testid="agent-status-running"
        title={strings.rightPanel.agents.running}
      >
        <Loader2 size={9} className="animate-spin" aria-hidden="true" />
        {strings.rightPanel.agents.running}
      </Badge>
    );
  }
  if (status === "done") {
    return (
      <Badge
        tone="success"
        data-testid="agent-status-done"
        title={strings.rightPanel.agents.done}
      >
        <CheckCircle2 size={9} aria-hidden="true" />
        {strings.rightPanel.agents.done}
      </Badge>
    );
  }
  return (
    <Badge
      tone="neutral"
      dot
      data-testid="agent-status-idle"
      title={strings.rightPanel.agents.idle}
    >
      {strings.rightPanel.agents.idle}
    </Badge>
  );
}
