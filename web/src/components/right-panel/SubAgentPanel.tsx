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
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Loader2,
  Sparkles,
  StopCircle,
} from "lucide-react";
import {
  useSubAgentStore,
  runsForSession,
  type SubAgentState,
} from "../../stores";
import type { ChildWorkspaceReview as ReviewResult, SubAgentRun, SubAgentStatus } from "../../types/ipc";
import { StructuredErrorCallout } from "../StructuredErrorCallout";
import { strings } from "../../ui/strings";
import { useSessionStore } from "../../stores/sessionStore";
import { ipc } from "../../ipc";

export interface SubAgentPanelProps {
  testId?: string;
  /** When provided, scopes the visible runs to this session. */
  sessionId?: string | null;
  /** Subscribe to the IPC progress event on mount. Defaults to true. */
  autoInit?: boolean;
  onOpenRun?: () => void;
}

const STATUS_TONE: Record<SubAgentStatus, { label: string; cls: string; icon: "spin" | "ok" | "err" | "idle" }> = {
  started: {
    label: strings.rightPanel.subagents.status.started,
    cls: "text-minimax-muted bg-minimax-border/40 border-minimax-border",
    icon: "idle",
  },
  thinking: {
    label: strings.rightPanel.subagents.status.thinking,
    cls: "text-minimax-accent bg-minimax-accent/10 border-minimax-accent/30",
    icon: "spin",
  },
  tool_call: {
    label: strings.rightPanel.subagents.status.tool_call,
    cls: "text-amber-300 bg-amber-500/10 border-amber-500/30",
    icon: "spin",
  },
  tool_result: {
    label: strings.rightPanel.subagents.status.tool_result,
    cls: "text-amber-300 bg-amber-500/10 border-amber-500/30",
    icon: "spin",
  },
  completed: {
    label: strings.rightPanel.subagents.status.completed,
    cls: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30",
    icon: "ok",
  },
  failed: {
    label: strings.rightPanel.subagents.status.failed,
    cls: "text-status-error bg-red-500/10 border-red-500/30",
    icon: "err",
  },
  cancelled: {
    label: strings.rightPanel.subagents.status.cancelled,
    cls: "text-minimax-muted bg-minimax-border/40 border-minimax-border",
    icon: "idle",
  },
};

/** Terminal states — sorted to the bottom and cleared by "Clear done". */
const isFinished = (s: SubAgentStatus) =>
  s === "completed" || s === "failed" || s === "cancelled";

export function SubAgentPanel({
  testId = "sub-agent-panel",
  sessionId,
  autoInit = true,
  onOpenRun,
}: SubAgentPanelProps): JSX.Element {
  const runsMap = useSubAgentStore((s) => s.runs);
  const init = useSubAgentStore((s) => s.init);
  const clearCompleted = useSubAgentStore((s) => s.clearCompleted);
  const currentSession = useSessionStore((s) => s.currentSessionId);
  const visibleSession = sessionId === undefined ? currentSession : sessionId;

  useEffect(() => {
    if (autoInit) void init();
  }, [autoInit, init]);

  const runs = useMemo<SubAgentRun[]>(
    () => runsForSession({ runs: runsMap, subscribed: true, error: null } as SubAgentState, visibleSession),
    [runsMap, visibleSession],
  );
  // Sort: in-flight first (oldest first), then finished (newest first).
  runs.sort((a, b) => {
    const aDone = isFinished(a.status);
    const bDone = isFinished(b.status);
    if (aDone !== bDone) return aDone ? 1 : -1;
    if (aDone) return b.updated_at - a.updated_at;
    return a.started_at - b.started_at;
  });

  const completedCount = runs.filter((r) => isFinished(r.status)).length;

  return (
    <div data-testid={testId} className="px-3 pb-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider text-minimax-muted">
          {runs.length === 0
            ? strings.rightPanel.subagents.idle
            : strings.rightPanel.subagents.runCount(runs.length)}
        </span>
        {completedCount > 0 && (
          <button
            type="button"
            data-testid={`${testId}-clear`}
            onClick={clearCompleted}
            className="text-[11px] text-minimax-muted hover:text-minimax-fg"
            title={strings.rightPanel.subagents.clearDoneTitle}
          >
            {strings.rightPanel.subagents.clearDone}
          </button>
        )}
      </div>
      {runs.length === 0 ? (
        <div
          data-testid={`${testId}-empty`}
          className="px-1 py-3 text-center text-[11px] italic text-minimax-muted"
        >
          空闲 — 在聊天中输入 <span className="font-mono">@general</span> 来召唤子 Agent。
        </div>
      ) : (
        <ul data-testid={`${testId}-list`} className="space-y-2">
          {runs.map((r) => (
            <SubAgentRow key={`${visibleSession}:${r.run_id}`} run={r} sessionId={visibleSession} testId={`${testId}-row`} onOpenRun={onOpenRun} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SubAgentRow({
  run,
  sessionId,
  testId,
  onOpenRun,
}: {
  run: SubAgentRun;
  sessionId: string | null;
  testId: string;
  onOpenRun?: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tone = STATUS_TONE[run.status];
  const pct = Math.max(0, Math.min(1, run.progress));
  return (
    <li
      data-testid={`${testId}-${run.run_id}`}
      className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
    >
      {/* Row header — clickable to expand/collapse. Using <div> instead of
          <button> to avoid nesting the cancel <button> inside a <button>. */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setExpanded((v) => !v); }}
        data-testid={`${testId}-${run.run_id}-header`}
        aria-expanded={expanded}
        className="flex w-full cursor-pointer items-center gap-2 text-left"
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
        {!isFinished(run.status) && !run.admission_unknown && (
          <button
            type="button"
            data-testid={`${testId}-${run.run_id}-cancel`}
            onClick={(e) => {
              e.stopPropagation();
              useSubAgentStore.getState().cancelRun(run.run_id);
            }}
            className="shrink-0 rounded-full p-0.5 text-minimax-muted transition-colors hover:bg-red-500/20 hover:text-status-error"
            title={strings.rightPanel.subagents.cancelTitle}
          >
            <StopCircle size={12} />
          </button>
        )}
        {expanded ? (
          <ChevronDown size={10} className="text-minimax-muted" />
        ) : (
          <ChevronRight size={10} className="text-minimax-muted" />
        )}
      </div>
      <div
        className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-minimax-border/60"
        aria-label={strings.rightPanel.subagents.progressAria}
        data-testid={`${testId}-${run.run_id}-progress-track`}
      >
        <div
          data-testid={`${testId}-${run.run_id}-progress-bar`}
          className={
            "h-full transition-all " +
            (run.status === "failed" || run.status === "cancelled"
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
          {run.run_status && <div>持久状态：{run.run_status}{run.partial ? " · 部分结果，需核对" : ""}</div>}
          {run.request && <div className="flex gap-2">
            {run.admission_unknown && <button type="button" onClick={() => void useSubAgentStore.getState().retry(run.run_id)}>使用原请求重试</button>}
            {run.session_id && <button type="button" onClick={() => void useSubAgentStore.getState().openRun(run.run_id).then((opened) => { if (opened) onOpenRun?.(); })}>查看任务</button>}
            <button type="button" onClick={() => void useSubAgentStore.getState().refresh()}>刷新结果</button>
          </div>}
          {isFinished(run.status) && sessionId && run.parent_session_id === sessionId && !run.request && (
            <ChildWorkspaceReview runId={run.run_id} sessionId={sessionId} />
          )}
          {run.error && (
            <StructuredErrorCallout
              testId={`${testId}-${run.run_id}-error`}
              title={strings.rightPanel.subagents.failedTitle}
              message={run.error}
              context={{
                run_id: run.run_id,
                agent_id: run.agent_id,
                agent_name: run.agent_name,
                parent_session_id: run.parent_session_id,
                context_message_id: run.context_message_id,
                prompt: run.prompt,
              }}
            />
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

function ChildWorkspaceReview({ runId, sessionId }: { runId: string; sessionId: string }): JSX.Element {
  const [review, setReview] = useState<ReviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const projectId = useSessionStore((s) => s.sessions.find((s) => s.id === sessionId)?.project_id ?? null);
  useEffect(() => {
    setReview(null); setMessage(""); setBusy(false);
    const epoch = generation;
    return () => { epoch.current++; };
  }, [sessionId, projectId]);
  const invoke = async (collect: boolean) => {
    const ticket = ++generation.current;
    const host = ipc.agentBaseUrl;
    setBusy(true); setMessage("");
    try {
      if (collect && !review) return;
      const result = await ipc.request<ReviewResult & { report?: { complete: boolean; conflicts: unknown[] } }>(
        "run.child_workspace", { run_id: runId, session_id: sessionId, action: collect ? "collect" : "preview",
          ...(collect && review ? { revision: review.revision, expected_service_id: review.service_id } : {}) },
      );
      if (ticket !== generation.current || host !== ipc.agentBaseUrl) return;
      if (result.session_id !== sessionId || result.project_id !== projectId || (collect && result.service_id !== review?.service_id)) throw new Error("会话、项目或服务已变化，请重新查看");
      if (collect) {
        setReview(null);
        setMessage(result.report?.complete ? "收集完成；原子任务工作区及备份仍保留。" : "未完整收集，请重新查看冲突。");
      } else setReview(result);
    } catch (error) {
      if (ticket === generation.current) {
        setReview(null);
        setMessage(`${error instanceof Error ? error.message : String(error)}${collect ? "。收集可能部分完成，请重新查看并核对文件。" : ""}`);
      }
    } finally { if (ticket === generation.current) setBusy(false); }
  };
  return <div className="space-y-1 border-t border-minimax-border/60 pt-1">
    <button type="button" disabled={busy} onClick={() => void invoke(false)}>查看子任务工作区结果</button>
    {message && <p role="status">{message}</p>}
    {review && <div className="space-y-1">
      <p>父目录：{review.root} · {review.kind} · {review.state}</p>
      <p>对比当前父目录与子任务结果；同期用户修改不归因于 Agent。普通独立目录不是操作系统沙盒。</p>
      {review.omitted.length > 0 && <p>未纳入：{review.omitted.join("、")}</p>}
      {review.files.length === 0 && <p>没有记录到文件变化。</p>}
      {review.files.map((file) => <details key={file.path}><summary>{file.path}{file.conflict ? ` · 冲突：${file.conflict}` : ""}</summary>
        {file.diff_unavailable ? <p>差异过大或非 UTF-8，请手动查看。</p> : <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{file.diff || "（内容相同或无文本差异）"}</pre>}
        {file.diff_truncated && <p>差异已截断，请手动查看。</p>}
      </details>)}
      <button type="button" disabled={busy || review.state === "collected" || review.files.some((f) => !!f.conflict)} onClick={() => void invoke(true)}>收集已审阅的无冲突变化</button>
      <p>删除和冲突需手动处理；执行中出现错误时可能部分收集，请核对文件。</p>
    </div>}
  </div>;
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
