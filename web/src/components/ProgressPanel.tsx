/**
 * Right-side progress panel — shows running long-tasks, sidecar
 * status, and the overall agent heartbeat. Collapsible; sticky to the
 * right edge of the chat area, overlapping the message list when
 * expanded.
 */
import { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Loader2,
  X,
} from "lucide-react";
import { ipc } from "../ipc";
import { useTaskStore } from "../stores";
import type { SidecarEvent } from "../types/ipc";

type SidecarState = "pending" | "ready" | "error";

export interface ProgressPanelProps {
  testId?: string;
}

export function ProgressPanel({ testId = "progress-panel" }: ProgressPanelProps): JSX.Element {
  const tasks = useTaskStore((s) => s.tasks);
  const collapsed = useTaskStore((s) => s.collapsed);
  const setCollapsed = useTaskStore((s) => s.setCollapsed);
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
  const hasContent = sidecar !== "ready" || sidecarDetail !== "" || taskList.length > 0;

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        data-testid={`${testId}-collapsed`}
        className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-md border border-minimax-border bg-minimax-panel px-2 py-1 text-xs text-minimax-fg shadow hover:border-minimax-accent/40"
      >
        <Activity size={12} />
        <span>
          {taskList.filter((t) => t.status === "running").length} running
        </span>
        <ChevronLeft size={12} />
      </button>
    );
  }

  return (
    <aside
      data-testid={testId}
      className="absolute right-3 top-3 z-10 w-72 rounded-lg border border-minimax-border bg-minimax-panel/95 shadow-xl backdrop-blur"
    >
      <header className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-minimax-fg">
          <Activity size={12} className="text-minimax-accent" />
          Progress
        </div>
        <button
          type="button"
          aria-label="Collapse progress panel"
          onClick={() => setCollapsed(true)}
          className="text-minimax-muted hover:text-minimax-fg"
        >
          <ChevronRight size={12} />
        </button>
      </header>

      <div className="space-y-3 p-3 text-xs">
        <Section
          title="Agent"
          icon={
            sidecar === "ready" ? (
              <CheckCircle2 size={12} className="text-emerald-400" />
            ) : sidecar === "error" ? (
              <CircleAlert size={12} className="text-red-400" />
            ) : (
              <Loader2 size={12} className="animate-spin text-amber-400" />
            )
          }
        >
          <div className="text-minimax-fg">
            {sidecar === "ready"
              ? "Connected"
              : sidecar === "error"
                ? "Error"
                : "Connecting…"}
          </div>
          {sidecarDetail && (
            <div className="mt-0.5 text-[10px] text-minimax-muted">{sidecarDetail}</div>
          )}
        </Section>

        {taskList.length > 0 && (
          <Section title={`Tasks (${taskList.length})`} icon={<Loader2 size={12} className="animate-spin" />}>
            <ul className="space-y-2">
              {taskList.map((t) => (
                <li
                  key={t.task_id}
                  data-testid={`task-row-${t.task_id}`}
                  className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[10px] text-minimax-muted">
                      {t.task_id}
                    </span>
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
          </Section>
        )}

        {!hasContent && (
          <div className="rounded-md border border-dashed border-minimax-border p-3 text-center text-minimax-muted">
            No active tasks. Long-running operations will show up here.
          </div>
        )}
      </div>
    </aside>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: JSX.Element;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1 text-[10px] uppercase tracking-wider text-minimax-muted">
        {icon}
        {title}
      </div>
      <div>{children}</div>
    </section>
  );
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
