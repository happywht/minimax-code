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
 * The contract preserved for backwards compatibility with the old
 * progress-panel.test.tsx is the ``testId`` namespace ``pp`` and the
 * ``task-row-<id>`` / ``task-status-<status>`` test ids on the inner
 * elements.
 */
import { useEffect, useState } from "react";
import { Activity, CheckCircle2, CircleAlert, Loader2, X } from "lucide-react";
import { ipc } from "../ipc";
import { useTaskStore } from "../stores";
import type { SidecarEvent } from "../types/ipc";

type SidecarState = "pending" | "ready" | "error";

export interface ProgressPanelProps {
  testId?: string;
}

export function ProgressPanel({ testId = "progress-panel" }: ProgressPanelProps): JSX.Element {
  const tasks = useTaskStore((s) => s.tasks);
  const startListening = useTaskStore((s) => s.startListening);
  const remove = useTaskStore((s) => s.remove);
  const [sidecar, setSidecar] = useState<SidecarState>("pending");
  const [sidecarDetail, setSidecarDetail] = useState("");

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
          setSidecarDetail(p.error || "agent crashed");
        } else {
          setSidecar("pending");
        }
      });
      // In mock mode the sidecar event never fires, but we treat the
      // client as "ready" the moment `start()` resolves.
      if (ipc.isMock) {
        setSidecar("ready");
        setSidecarDetail("mock backend");
      }
    })();
    return () => {
      unlisten?.();
    };
  }, []);

  const taskList = Object.values(tasks);
  const runningCount = taskList.filter((t) => t.status === "running").length;

  return (
    <section
      data-testid={testId}
      className="flex flex-col gap-2 text-xs"
    >
      <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-minimax-fg">
          <Activity size={12} className="text-minimax-accent" />
          Progress
          {runningCount > 0 && (
            <span
              data-testid={`${testId}-running-count`}
              className="ml-1 rounded bg-minimax-accent/20 px-1.5 text-[10px] text-minimax-accent"
            >
              {runningCount} running
            </span>
          )}
        </div>
        <AgentStatusInline state={sidecar} detail={sidecarDetail} />
      </div>

      <div className="space-y-2 px-3 pb-3">
        {taskList.length === 0 ? (
          <div
            data-testid={`${testId}-empty`}
            className="rounded-md border border-dashed border-minimax-border p-3 text-center text-[11px] text-minimax-muted"
          >
            No active tasks
          </div>
        ) : (
          <ul className="space-y-1.5" data-testid={`${testId}-task-list`}>
            {taskList.map((t) => (
              <li
                key={t.task_id}
                data-testid={`task-row-${t.task_id}`}
                className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <StatusDot status={t.status} />
                    <span className="truncate font-mono text-[10px] text-minimax-muted">
                      {t.task_id}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    <StatusBadge status={t.status} />
                    <button
                      type="button"
                      aria-label="Dismiss task"
                      onClick={() => remove(t.task_id)}
                      className="text-minimax-muted hover:text-minimax-fg"
                    >
                      <X size={10} />
                    </button>
                  </div>
                </div>
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-minimax-border">
                  <div
                    className={
                      "h-full transition-all " +
                      (t.status === "error"
                        ? "bg-red-500"
                        : t.status === "done"
                          ? "bg-emerald-500"
                          : "bg-minimax-accent")
                    }
                    style={{ width: `${Math.round(t.progress * 100)}%` }}
                  />
                </div>
                {t.message && (
                  <div className="mt-1 truncate text-[10px] text-minimax-muted">
                    {t.message}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
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
        className="flex items-center gap-1 text-[10px] text-emerald-400"
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
        className="flex items-center gap-1 text-[10px] text-red-400"
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
      className="flex items-center gap-1 text-[10px] text-amber-400"
      title={detail}
    >
      <Loader2 size={10} className="animate-spin" />
      Agent
    </span>
  );
}

function StatusDot({ status }: { status: string }): JSX.Element {
  if (status === "running") {
    return <Loader2 size={10} className="animate-spin text-amber-400" />;
  }
  if (status === "done") {
    return <CheckCircle2 size={10} className="text-emerald-400" />;
  }
  if (status === "error") {
    return <CircleAlert size={10} className="text-red-400" />;
  }
  return <span className="block h-2 w-2 rounded-full bg-minimax-muted" />;
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  const map: Record<string, string> = {
    running: "bg-amber-500/20 text-amber-300",
    done: "bg-emerald-500/20 text-emerald-300",
    error: "bg-red-500/20 text-red-300",
    cancelled: "bg-minimax-border text-minimax-muted",
  };
  return (
    <span
      data-testid={`task-status-${status}`}
      className={
        "rounded px-1.5 py-0.5 text-[9px] font-medium uppercase " +
        (map[status] ?? "bg-minimax-border text-minimax-muted")
      }
    >
      {status}
    </span>
  );
}
