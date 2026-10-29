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
import { useSessionStore } from "../../stores/sessionStore";
import { ChildWorkspaceReview } from "./SubAgentPanel";

function statusIcon(status: TeamRunEntry["status"]) {
  switch (status) {
    case "started":
    case "agent_started":
    case "agent_completed":
      return <Loader2 size={12} className="animate-spin text-minimax-accent" />;
    case "completed":
      return <CheckCircle2 size={12} className="text-status-success" />;
    case "failed":
    case "interrupted":
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

function RunCard({ run, onRemove, onCancel }: { run: TeamRunEntry; onRemove: () => void; onCancel: () => void }) {
  const isDone = run.status === "completed" || run.status === "failed" || run.status === "interrupted";
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
          {!isDone && !run.task_id.startsWith("pending-") && (
            <button type="button" disabled={run.cancelling} onClick={onCancel}
              className="rounded border border-line px-2 py-1 text-ink-1 disabled:opacity-50">
              {run.cancelling ? "正在停止…" : "停止团队"}
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {!isDone && <ProgressBar progress={run.progress} />}
      {run.status === "interrupted" && <p className="mt-1 text-status-error">已中断；请核对执行中的操作和文件变化，不能自动恢复。</p>}

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
      {isDone && <div className="mt-1 text-[11px] text-minimax-muted">
        {run.result?.budget
          ? <>已知 token 下界 {run.result.budget.known_tokens}；预留 {run.result.budget.reserved_tokens}；
              未知请求 {run.result.budget.unknown_attempts}；
              {run.result.budget.usage_complete ? "用量已记录" : "用量未完整确认"}；
              上限 {run.result.budget.limit_tokens ?? "未设置"}</>
          : "预算用量快照不可用"}
      </div>}
      {run.result?.merged_text && isDone && (
        <p className="mt-1 line-clamp-3 text-[11px] text-minimax-muted">
          {run.result.merged_text.slice(0, 300)}
        </p>
      )}
      {isDone && run.session_id && run.result?.agents_run.map((agent, index) => agent.workspace?.run_id &&
        <details key={`${agent.workspace.run_id}-${index}`} className="mt-2"><summary>{agent.agent_name} · 审阅独立工作区</summary>
          <ChildWorkspaceReview runId={agent.workspace.run_id} sessionId={run.session_id!} teamRunId={run.task_id} />
        </details>)}
    </div>
  );
}

/** One row in the persisted-runs history section. */
function HistoryRow({ run }: { run: AgentRun }): JSX.Element {
  const [stopMessage, setStopMessage] = useState("");
  const [stopping, setStopping] = useState(false);
  const done = run.status === "completed";
  const teamResult = run.metadata?.team_result as { budget?: { known_tokens: number; reserved_tokens: number; unknown_attempts: number; usage_complete: boolean } | null; agents_run?: Array<{ agent_name: string; workspace?: { run_id: string } }> } | undefined;
  const budget = teamResult?.budget;
  return (
    <li
      data-testid={`teamrun-history-${run.id}`}
      className="rounded-md border border-line/60 bg-surface-2/50 px-2 py-1.5 text-[11px]"
    >
      <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 items-center gap-1.5">
        {done ? (
          <CheckCircle2 size={11} className="shrink-0 text-status-success" />
        ) : (
          <XCircle size={11} className="shrink-0 text-status-error" />
        )}
        <span className="min-w-0 text-ink-0">{run.title || run.id} · {run.status === "interrupted" ? "已中断，需核对" : run.status}
          {budget && <span className="block text-ink-2">结束时快照：已知下界 {budget.known_tokens}，预留 {budget.reserved_tokens}，未知请求 {budget.unknown_attempts}；{budget.usage_complete ? "用量已记录" : "用量未完整确认"}</span>}
          {stopMessage && <span className="block text-ink-2">{stopMessage}</span>}
        </span>
      </span>
      {run.status === "running" && <button type="button" disabled={stopping} className="rounded border border-line px-2 py-1 disabled:opacity-50" onClick={() => {
        if (useSessionStore.getState().currentSessionId !== run.session_id) return;
        setStopping(true);
        void typedIPC.cancelRun(run.id).then((result) => {
          setStopMessage(result.requested ? "已请求停止，等待清理；请刷新记录。" : "服务中没有可停止的运行；请刷新记录。");
        }).catch((error) => setStopMessage(`停止请求失败：${String(error)}`)).finally(() => setStopping(false));
      }}>停止</button>}
      <span className="shrink-0 text-ink-2">{formatRelative(run.created_at)}</span>
      </div>
      {teamResult?.agents_run?.map((agent, index) => agent.workspace?.run_id &&
        <details key={`${agent.workspace.run_id}-${index}`} className="mt-2"><summary>{agent.agent_name} · 审阅独立工作区</summary>
          <ChildWorkspaceReview runId={agent.workspace.run_id} sessionId={run.session_id} teamRunId={run.id} />
        </details>)}
    </li>
  );
}

export function TeamRunPanel(): JSX.Element {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const runs = useTeamRunStore((s) => s.runs);
  const cancel = useTeamRunStore((s) => s.cancel);
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
    setHistory([]);
    if (!currentSessionId) return;
    typedIPC
      .listRuns({ session_id: currentSessionId, limit: 30 })
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
  }, [currentSessionId]);

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

  const visibleRuns = runs.filter((r) => r.session_id === currentSessionId);
  if (visibleRuns.length === 0) {
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
          {strings.rightPanel.teamRuns.activeCount(visibleRuns.length)}
        </span>
      </div>
      <div className="space-y-1.5">
        {visibleRuns.map((r) => (
          <RunCard key={r.task_id} run={r} onRemove={() => remove(r.task_id)} onCancel={() => void cancel(r.task_id)} />
        ))}
      </div>
      {historySection}
    </div>
  );
}
