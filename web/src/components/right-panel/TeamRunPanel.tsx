/**
 * TeamRunPanel — visualises live team spawn runs in the right panel.
 *
 * Shows a list of team runs with progress bars, agent counts, and
 * conflict warnings. Auto-prunes completed runs after 60 s.
 *
 * v0.8.0 — Enterprise Multi-Agent.
 */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  History,
  Loader2,
  Users,
  X,
  XCircle,
} from "lucide-react";
import {
  useTeamRunStore,
  initTeamRunListener,
  type TeamRunEntry,
} from "../../stores/teamRunStore";
import { typedIPC } from "../../ipc";
import { formatRelative } from "../../lib/time";
import { strings } from "../../ui/strings";
import type { AgentRun } from "../../types/ipc";

function statusIcon(status: TeamRunEntry["status"]) {
  switch (status) {
    case "started":
    case "agent_started":
    case "agent_completed":
      return <Loader2 size={12} className="animate-spin text-minimax-accent" />;
    case "completed":
      return <CheckCircle2 size={12} className="text-status-success" />;
    case "failed":
      return <XCircle size={12} className="text-status-error" />;
    default:
      return null;
  }
}

function ProgressBar({ progress }: { progress: number }) {
  const pct = Math.round(progress * 100);
  return (
    <div className="h-1 w-full rounded-full bg-minimax-border">
      <div
        className="h-1 rounded-full bg-minimax-accent transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function RunCard({ run, onRemove }: { run: TeamRunEntry; onRemove: () => void }) {
  const isDone = run.status === "completed" || run.status === "failed";
  const hasConflicts = run.result?.conflicts?.length;

  return (
    <div
      data-testid={`teamrun-${run.task_id}`}
      className={
        "rounded-md border p-2 text-xs " +
        (isDone
          ? "border-minimax-border bg-minimax-panel/30"
          : "border-minimax-accent/30 bg-minimax-accent/5")
      }
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {statusIcon(run.status)}
          <Users size={11} className="text-minimax-accent" />
          <span className="font-medium text-minimax-fg">{run.team_name}</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-minimax-muted">
            {strings.rightPanel.teamRuns.agentCount(run.agents_completed, run.agents_total)}
          </span>
          {isDone && (
            <button
              type="button"
              onClick={onRemove}
              className="rounded p-0.5 text-minimax-muted hover:text-minimax-fg"
              aria-label={strings.rightPanel.teamRuns.removeAria}
            >
              <X size={10} />
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {!isDone && <ProgressBar progress={run.progress} />}

      {/* Active agent */}
      {run.agent_name && !isDone && (
        <div className="mt-1 text-[11px] text-minimax-muted">
          {strings.rightPanel.teamRuns.running(run.agent_name)}
        </div>
      )}

      {/* Conflicts */}
      {hasConflicts ? (
        <div className="mt-1 flex items-center gap-1 text-[11px] text-yellow-300">
          <AlertTriangle size={10} />
          <span>
            {strings.rightPanel.teamRuns.conflicts(run.result!.conflicts.length)}
          </span>
        </div>
      ) : null}

      {/* Summary for completed */}
      {run.result?.merged_text && isDone && (
        <p className="mt-1 line-clamp-3 text-[11px] text-minimax-muted">
          {run.result.merged_text.slice(0, 300)}
        </p>
      )}
    </div>
  );
}

/** One row in the persisted-runs history section. */
function HistoryRow({ run }: { run: AgentRun }): JSX.Element {
  const done = run.status === "completed";
  return (
    <li
      data-testid={`teamrun-history-${run.id}`}
      className="flex items-center justify-between gap-2 rounded-md border border-line/60 bg-surface-2/50 px-2 py-1.5 text-[11px]"
    >
      <span className="flex min-w-0 items-center gap-1.5">
        {done ? (
          <CheckCircle2 size={11} className="shrink-0 text-status-success" />
        ) : (
          <XCircle size={11} className="shrink-0 text-status-error" />
        )}
        <span className="truncate text-ink-0">{run.title || run.id}</span>
      </span>
      <span className="shrink-0 text-ink-2">{formatRelative(run.created_at)}</span>
    </li>
  );
}

export function TeamRunPanel(): JSX.Element {
  const runs = useTeamRunStore((s) => s.runs);
  const prune = useTeamRunStore((s) => s.prune);
  const remove = useTeamRunStore((s) => s.remove);
  const [history, setHistory] = useState<AgentRun[]>([]);

  // Init WS listener on first mount
  useEffect(() => {
    const unsub = initTeamRunListener();
    return unsub;
  }, []);

  // Auto-prune completed runs every 30 s
  useEffect(() => {
    const id = setInterval(() => prune(60_000), 30_000);
    return () => clearInterval(id);
  }, [prune]);

  // Load the persisted team-run history once on mount. run.list returns
  // runs of every mode, so filter to mode === "team" and keep the latest 5.
  useEffect(() => {
    let cancelled = false;
    typedIPC
      .listRuns({ limit: 30 })
      .then((result) => {
        if (cancelled) return;
        setHistory(result.runs.filter((r) => r.mode === "team").slice(0, 5));
      })
      .catch(() => {
        // History is best-effort; the live section still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const historySection = history.length > 0 && (
    <div data-testid="teamrun-history" className="mt-2 border-t border-line/50 pt-2">
      <h4 className="mb-1 flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide text-ink-2">
        <History size={10} />
        {strings.rightPanel.teamRuns.historyTitle}
      </h4>
      <ul className="space-y-1">
        {history.map((r) => (
          <HistoryRow key={r.id} run={r} />
        ))}
      </ul>
    </div>
  );

  if (runs.length === 0) {
    return (
      <div data-testid="team-run-panel-empty" className="space-y-1.5 text-center">
        <div className="flex justify-center">
          <Users size={14} className="text-minimax-muted" />
        </div>
        <p className="text-[11px] text-minimax-fg">{strings.rightPanel.teamRuns.emptyTitle}</p>
        <p className="text-[11px] text-minimax-muted">{strings.rightPanel.teamRuns.emptyHint}</p>
        {historySection}
      </div>
    );
  }

  return (
    <div data-testid="team-run-panel" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] font-medium text-minimax-fg">
          <Users size={11} className="mr-1 inline text-minimax-accent" />
          {strings.rightPanel.teamRuns.title}
        </h3>
        <span className="text-[11px] text-minimax-muted">
          {strings.rightPanel.teamRuns.activeCount(runs.length)}
        </span>
      </div>
      <div className="space-y-1.5">
        {runs.map((r) => (
          <RunCard key={r.task_id} run={r} onRemove={() => remove(r.task_id)} />
        ))}
      </div>
      {historySection}
    </div>
  );
}
