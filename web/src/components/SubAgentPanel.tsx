/**
 * SubAgentPanel — right-rail live list of in-flight sub-agent runs.
 *
 * Lives in the RightPanel (a new section next to Progress / Agent
 * Team) when the design permits, but this component is
 * self-contained: it just reads from ``useSubAgentStore`` and
 * renders rows.
 *
 * Per row: agent name + status pill + progress bar + last summary.
 * Completed / failed runs collapse to a single line so the panel
 * doesn't grow unbounded.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Loader2,
  Sparkles,
} from "lucide-react";
import {
  useSubAgentStore,
  runsForSession,
  type SubAgentState,
} from "../stores";
import type { SubAgentRun, SubAgentStatus } from "../types/ipc";

export interface SubAgentPanelProps {
  testId?: string;
  /** When provided, scopes the visible runs to this session. */
  sessionId?: string | null;
  /** Subscribe to the IPC progress event on mount. Defaults to true. */
  autoInit?: boolean;
}

const STATUS_TONE: Record<SubAgentStatus, { label: string; cls: string; icon: "spin" | "ok" | "err" | "idle" }> = {
  started: { label: "Started", cls: "text-minimax-muted bg-minimax-border/40 border-minimax-border", icon: "idle" },
  thinking: { label: "Thinking", cls: "text-minimax-accent bg-minimax-accent/10 border-minimax-accent/30", icon: "spin" },
  tool_call: { label: "Tool call", cls: "text-amber-300 bg-amber-500/10 border-amber-500/30", icon: "spin" },
  tool_result: { label: "Tool result", cls: "text-amber-300 bg-amber-500/10 border-amber-500/30", icon: "spin" },
  completed: { label: "Completed", cls: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30", icon: "ok" },
  failed: { label: "Failed", cls: "text-red-300 bg-red-500/10 border-red-500/30", icon: "err" },
};

export function SubAgentPanel({
  testId = "sub-agent-panel",
  sessionId,
  autoInit = true,
}: SubAgentPanelProps): JSX.Element {
  const runsMap = useSubAgentStore((s) => s.runs);
  const init = useSubAgentStore((s) => s.init);
  const clearCompleted = useSubAgentStore((s) => s.clearCompleted);

  useEffect(() => {
    if (autoInit) void init();
  }, [autoInit, init]);

  const runs = useMemo<SubAgentRun[]>(
    () => runsForSession({ runs: runsMap, subscribed: true, error: null } as SubAgentState, sessionId),
    [runsMap, sessionId],
  );
  // Sort: in-flight first (oldest first), then completed/failed (newest first).
  runs.sort((a, b) => {
    const aDone = a.status === "completed" || a.status === "failed";
    const bDone = b.status === "completed" || b.status === "failed";
    if (aDone !== bDone) return aDone ? 1 : -1;
    if (aDone) return b.updated_at - a.updated_at;
    return a.started_at - b.started_at;
  });

  const completedCount = runs.filter(
    (r) => r.status === "completed" || r.status === "failed",
  ).length;

  return (
    <div data-testid={testId} className="px-3 pb-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider text-minimax-muted">
          {runs.length === 0 ? "Idle" : `${runs.length} run${runs.length === 1 ? "" : "s"}`}
        </span>
        {completedCount > 0 && (
          <button
            type="button"
            data-testid={`${testId}-clear`}
            onClick={clearCompleted}
            className="text-[11px] text-minimax-muted hover:text-minimax-fg"
            title="Drop completed / failed runs from the list"
          >
            Clear done
          </button>
        )}
      </div>
      {runs.length === 0 ? (
        <div
          data-testid={`${testId}-empty`}
          className="px-1 py-3 text-center text-[11px] italic text-minimax-muted"
        >
          Idle — type <span className="font-mono">@general</span> in chat to spawn.
        </div>
      ) : (
        <ul data-testid={`${testId}-list`} className="space-y-2">
          {runs.map((r) => (
            <SubAgentRow key={r.run_id} run={r} testId={`${testId}-row`} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SubAgentRow({
  run,
  testId,
}: {
  run: SubAgentRun;
  testId: string;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tone = STATUS_TONE[run.status];
  const pct = Math.max(0, Math.min(1, run.progress));
  return (
    <li
      data-testid={`${testId}-${run.run_id}`}
      className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        data-testid={`${testId}-${run.run_id}-header`}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 text-left"
      >
        <span
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-minimax-accent/20 text-minimax-accent"
          aria-hidden="true"
        >
          <Bot size={10} />
        </span>
        <span
          data-testid={`${testId}-${run.run_id}-name`}
          className="min-w-0 flex-1 truncate text-[11px] font-medium text-minimax-fg"
          title={run.display_name || run.agent_name}
        >
          {run.display_name || run.agent_name}
        </span>
        <StatusPill tone={tone} testId={`${testId}-${run.run_id}-status`} />
        {expanded ? (
          <ChevronDown size={10} className="text-minimax-muted" />
        ) : (
          <ChevronRight size={10} className="text-minimax-muted" />
        )}
      </button>
      <div
        className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-minimax-border/60"
        aria-label="progress"
        data-testid={`${testId}-${run.run_id}-progress-track`}
      >
        <div
          data-testid={`${testId}-${run.run_id}-progress-bar`}
          className={
            "h-full transition-all " +
            (run.status === "failed"
              ? "bg-red-400"
              : run.status === "completed"
                ? "bg-emerald-400"
                : "bg-minimax-accent")
          }
          style={{ width: `${Math.round(pct * 100)}%` }}
        />
      </div>
      <div
        data-testid={`${testId}-${run.run_id}-summary`}
        className="mt-1 truncate text-[11px] text-minimax-muted"
        title={run.summary}
      >
        {run.summary || <span className="italic">…</span>}
      </div>
      {expanded && (
        <div
          data-testid={`${testId}-${run.run_id}-detail`}
          className="mt-2 space-y-1 border-t border-minimax-border/60 pt-2 text-[11px] text-minimax-muted"
        >
          <div>
            <span className="text-minimax-fg/80">run_id:</span>{" "}
            <span className="font-mono">{run.run_id}</span>
          </div>
          {run.parent_session_id && (
            <div>
              <span className="text-minimax-fg/80">parent:</span>{" "}
              <span className="font-mono">{run.parent_session_id}</span>
            </div>
          )}
          {run.context_message_id && (
            <div>
              <span className="text-minimax-fg/80">context:</span>{" "}
              <span className="font-mono">{run.context_message_id}</span>
            </div>
          )}
          {run.prompt && (
            <div className="whitespace-pre-wrap break-words">
              <span className="text-minimax-fg/80">prompt:</span>{" "}
              {run.prompt}
            </div>
          )}
          {run.error && (
            <div className="text-red-300" data-testid={`${testId}-${run.run_id}-error`}>
              {run.error}
            </div>
          )}
          {run.text && (
            <div
              className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded border border-minimax-border/60 bg-minimax-bg/50 p-1.5 text-minimax-fg/90"
              data-testid={`${testId}-${run.run_id}-text`}
            >
              {run.text}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function StatusPill({
  tone,
  testId,
}: {
  tone: typeof STATUS_TONE[SubAgentStatus];
  testId: string;
}): JSX.Element {
  const Icon =
    tone.icon === "spin" ? Loader2 : tone.icon === "ok" ? CheckCircle2 : tone.icon === "err" ? CircleAlert : Sparkles;
  return (
    <span
      data-testid={testId}
      className={
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px] " +
        tone.cls
      }
    >
      <Icon
        size={9}
        className={tone.icon === "spin" ? "animate-spin" : undefined}
      />
      {tone.label}
    </span>
  );
}
