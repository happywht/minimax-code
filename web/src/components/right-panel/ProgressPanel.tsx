/**
 * ProgressPanel — content-only progress list for the right column.
 *
 * Historical role: this used to be a floating, collapsible overlay pinned
 * to the right edge of the chat area. Phase 3 collapsed the layout into
 * a true three-pane shell, so the panel now lives inside the dedicated
 * ``<RightPanel />`` and is responsible only for the *list* of running
 * tasks (the chrome — header, collapse, agent status — is owned by the
 * container).
 *
 * v0.11.0: the panel now hydrates from the persisted ``task.list`` ledger
 * on mount and exposes a cancel affordance for running tasks.
 *
 * B3 redesign: the ledger's ``title`` becomes the primary identifier
 * (the mono task id moves to a metadata line), tasks are partitioned
 * into live / settled sections (newest first), status badges are
 * localized via strings.ts, each card shows relative time + settled
 * duration + a progress percentage, and result / error bodies are
 * expandable instead of hard-truncated at max-h-16.
 *
 * The contract preserved for backwards compatibility with the old
 * progress-panel.test.tsx is the ``testId`` namespace ``pp`` and the
 * ``task-row-<id>`` / ``task-status-<status>`` test ids on the inner
 * elements.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Activity, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Loader2, RefreshCw, X, Ban } from "lucide-react";
import { ipc } from "../../ipc";
import { useTaskStore, type TaskProgressEntry } from "../../stores";
import type { SidecarEvent } from "../../types/ipc";
import { strings } from "../../ui/strings";
import { formatDuration, formatRelative } from "../../lib/time";

type SidecarState = "pending" | "ready" | "error";

export interface ProgressPanelProps {
  testId?: string;
}

/** Split entries into live (running/pending) and settled, newest first. */
function partitionTasks(
  entries: TaskProgressEntry[],
): { live: TaskProgressEntry[]; settled: TaskProgressEntry[] } {
  const live: TaskProgressEntry[] = [];
  const settled: TaskProgressEntry[] = [];
  for (const t of entries) {
    if (t.status === "running" || t.status === "pending") {
      live.push(t);
    } else {
      settled.push(t);
    }
  }
  const byNewest = (a: TaskProgressEntry, b: TaskProgressEntry) => b.updated_at - a.updated_at;
  live.sort(byNewest);
  settled.sort(byNewest);
  return { live, settled };
}

export function ProgressPanel({ testId = "progress-panel" }: ProgressPanelProps): JSX.Element {
  const tasks = useTaskStore((s) => s.tasks);
  const startListening = useTaskStore((s) => s.startListening);
  const refresh = useTaskStore((s) => s.refresh);
  const cancel = useTaskStore((s) => s.cancel);
  const remove = useTaskStore((s) => s.remove);
  const [sidecar, setSidecar] = useState<SidecarState>("pending");
  const [sidecarDetail, setSidecarDetail] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  // Wire task.progress events and sidecar events once on mount.
  useEffect(() => {
    const unsub = startListening();
    return () => unsub();
  }, [startListening]);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    (async () => {
      await ipc.start();
      unlisten = ipc.onSideCar((p: SidecarEvent) => {
        if (p.status === "started") {
          setSidecar("ready");
          setSidecarDetail("");
        } else if (p.status === "error") {
          setSidecar("error");
          setSidecarDetail(p.error || strings.rightPanel.progress.agentCrashed);
        } else {
          setSidecar("pending");
        }
      });
      // In mock mode the sidecar event never fires, but we treat the
      // client as "ready" the moment `start()` resolves.
      if (ipc.isMock) {
        setSidecar("ready");
        setSidecarDetail(strings.rightPanel.progress.mockBackend);
      }
    })();
    return () => {
      unlisten?.();
    };
  }, []);

  // Hydrate from the persisted task ledger on mount.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  };

  const handleCancel = async (taskId: string) => {
    await cancel(taskId);
  };

  const taskList = Object.values(tasks);
  const runningCount = taskList.filter((t) => t.status === "running").length;
  const { live, settled } = partitionTasks(taskList);

  return (
    <section
      data-testid={testId}
      className="flex flex-col gap-2 text-xs"
    >
      <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-minimax-fg">
          <Activity size={12} className="text-minimax-accent" />
          {strings.rightPanel.progress.title}
          {runningCount > 0 && (
            <span
              data-testid={`${testId}-running-count`}
              className="ml-1 rounded bg-minimax-accent/20 px-1.5 text-[11px] text-minimax-accent"
            >
              {strings.rightPanel.progress.runningCount(runningCount)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            title={strings.rightPanel.progress.refreshTitle}
            onClick={() => void handleRefresh()}
            disabled={refreshing}
            className="text-minimax-muted hover:text-minimax-fg disabled:opacity-50"
            data-testid={`${testId}-refresh`}
          >
            <RefreshCw size={10} className={refreshing ? "animate-spin" : ""} />
          </button>
          <AgentStatusInline state={sidecar} detail={sidecarDetail} />
        </div>
      </div>

      <div className="space-y-2 px-3 pb-3">
        {taskList.length === 0 ? (
          <div
            data-testid={`${testId}-empty`}
            className="rounded-md border border-dashed border-minimax-border p-3 text-center text-[11px] text-minimax-muted"
          >
            {strings.rightPanel.progress.empty}
          </div>
        ) : (
          <>
            {live.length > 0 && (
              <TaskSection
                label={strings.rightPanel.progress.sectionRunning}
                listTestId={`${testId}-task-list-live`}
              >
                {live.map((t) => (
                  <TaskCard
                    key={t.task_id}
                    task={t}
                    testId={testId}
                    onCancel={() => void handleCancel(t.task_id)}
                    onDismiss={() => remove(t.task_id)}
                  />
                ))}
              </TaskSection>
            )}
            {settled.length > 0 && (
              <TaskSection
                label={strings.rightPanel.progress.sectionSettled}
                listTestId={`${testId}-task-list-settled`}
              >
                {settled.map((t) => (
                  <TaskCard
                    key={t.task_id}
                    task={t}
                    testId={testId}
                    onCancel={() => void handleCancel(t.task_id)}
                    onDismiss={() => remove(t.task_id)}
                  />
                ))}
              </TaskSection>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function TaskSection({
  label,
  listTestId,
  children,
}: {
  label: string;
  listTestId: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div>
      <div className="px-0.5 pb-1 text-[11px] font-medium uppercase tracking-wide text-minimax-muted">
        {label}
      </div>
      <ul className="space-y-1.5" data-testid={listTestId}>
        {children}
      </ul>
    </div>
  );
}

function TaskCard({
  task,
  testId,
  onCancel,
  onDismiss,
}: {
  task: TaskProgressEntry;
  testId: string;
  onCancel: () => void;
  onDismiss: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const pct = Math.round(task.progress * 100);
  const settled = task.status !== "running" && task.status !== "pending";
  const detail = task.status === "error" ? task.message || task.result : task.result;

  return (
    <li
      data-testid={`task-row-${task.task_id}`}
      className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <StatusDot status={task.status} />
          <span
            data-testid={`task-title-${task.task_id}`}
            className="truncate text-[11px] text-minimax-fg"
            title={task.title || task.task_id}
          >
            {task.title || task.task_id}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={task.status} />
          {task.status === "running" && (
            <button
              type="button"
              aria-label={strings.rightPanel.progress.cancelTitle}
              title={strings.rightPanel.progress.cancelTitle}
              onClick={onCancel}
              className="text-minimax-muted hover:text-status-error"
              data-testid={`${testId}-cancel-${task.task_id}`}
            >
              <Ban size={10} />
            </button>
          )}
          <button
            type="button"
            aria-label={strings.rightPanel.progress.dismissTitle}
            title={strings.rightPanel.progress.dismissTitle}
            onClick={onDismiss}
            className="text-minimax-muted hover:text-minimax-fg"
          >
            <X size={10} />
          </button>
        </div>
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-[11px] text-minimax-muted">
        <span className="truncate font-mono">{task.task_id}</span>
        <span aria-hidden>·</span>
        <span className="whitespace-nowrap">{formatRelative(task.updated_at)}</span>
        {settled && task.created_at !== undefined && (
          <>
            <span aria-hidden>·</span>
            <span className="whitespace-nowrap" data-testid={`task-duration-${task.task_id}`}>
              {strings.rightPanel.progress.durationPrefix}{" "}
              {formatDuration(task.updated_at - task.created_at)}
            </span>
          </>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <div className="h-1 flex-1 overflow-hidden rounded bg-minimax-border">
          <div
            className={
              "h-full transition-all " +
              (task.status === "error"
                ? "bg-red-500"
                : task.status === "done"
                  ? "bg-emerald-500"
                  : "bg-minimax-accent")
            }
            style={{ width: `${pct}%` }}
          />
        </div>
        <span
          data-testid={`task-percent-${task.task_id}`}
          className="w-8 shrink-0 text-right font-mono text-[11px] text-minimax-muted"
        >
          {strings.rightPanel.progress.percent(pct)}
        </span>
      </div>
      {task.message && task.status !== "error" && (
        <div className="mt-1 truncate text-[11px] text-minimax-muted">{task.message}</div>
      )}
      {detail && (
        <button
          type="button"
          data-testid={`task-detail-toggle-${task.task_id}`}
          aria-expanded={expanded}
          title={expanded ? strings.rightPanel.progress.collapseTitle : strings.rightPanel.progress.expandTitle}
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 block w-full text-left"
        >
          <div
            data-testid={`task-detail-${task.task_id}`}
            className={
              "whitespace-pre-wrap break-words rounded bg-minimax-bg/60 px-1.5 py-1 font-mono text-[11px] leading-4 text-minimax-fg/70 " +
              (expanded ? "" : "line-clamp-2")
            }
          >
            {detail}
          </div>
          <div className="flex items-center gap-0.5 pt-0.5 text-[11px] text-minimax-muted">
            {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
            {expanded ? strings.rightPanel.progress.collapseTitle : strings.rightPanel.progress.expandTitle}
          </div>
        </button>
      )}
    </li>
  );
}

function AgentStatusInline({
  state,
  detail,
}: {
  state: SidecarState;
  detail: string;
}): JSX.Element {
  if (state === "ready") {
    return (
      <span
        data-testid="progress-agent-status"
        className="flex items-center gap-1 text-[11px] text-status-success"
        title={detail}
      >
        <CheckCircle2 size={10} />
        Agent
      </span>
    );
  }
  if (state === "error") {
    return (
      <span
        data-testid="progress-agent-status"
        className="flex items-center gap-1 text-[11px] text-status-error"
        title={detail}
      >
        <CircleAlert size={10} />
        Agent
      </span>
    );
  }
  return (
    <span
      data-testid="progress-agent-status"
      className="flex items-center gap-1 text-[11px] text-status-warning"
      title={detail}
    >
      <Loader2 size={10} className="animate-spin" />
      Agent
    </span>
  );
}

function StatusDot({ status }: { status: string }): JSX.Element {
  if (status === "running") {
    return <Loader2 size={10} className="animate-spin text-status-warning" />;
  }
  if (status === "done") {
    return <CheckCircle2 size={10} className="text-status-success" />;
  }
  if (status === "error") {
    return <CircleAlert size={10} className="text-status-error" />;
  }
  return <span className="block h-2 w-2 rounded-full bg-minimax-muted" />;
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  const map: Record<string, string> = {
    running: "bg-amber-500/20 text-amber-300",
    done: "bg-emerald-500/20 text-emerald-300",
    error: "bg-red-500/20 text-status-error",
    cancelled: "bg-minimax-border text-minimax-muted",
    pending: "bg-minimax-border text-minimax-muted",
  };
  return (
    <span
      data-testid={`task-status-${status}`}
      className={
        "rounded px-1.5 py-0.5 text-[11px] font-medium " +
        (map[status] ?? "bg-minimax-border text-minimax-muted")
      }
    >
      {strings.rightPanel.progress.statusLabel[status] ?? status}
    </span>
  );
}
