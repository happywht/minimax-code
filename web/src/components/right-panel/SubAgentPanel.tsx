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
import type { ChildWorkspaceReceipt, ChildWorkspaceReview as ReviewResult, SubAgentRun, SubAgentStatus } from "../../types/ipc";
import { StructuredErrorCallout } from "../StructuredErrorCallout";
import { strings } from "../../ui/strings";
import { useSessionStore } from "../../stores/sessionStore";
import { ipc } from "../../ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";

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
  unknown: {
    label: "状态待核对",
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
  const currentProject = useSessionStore((s) => s.sessions.find((row) => row.id === (sessionId === undefined ? s.currentSessionId : sessionId))?.project_id);
  const recoverChildren = useSubAgentStore((s) => s.recoverChildren);
  const recoveryError = useSubAgentStore((s) => s.recoveryError);
  const visibleSession = sessionId === undefined ? currentSession : sessionId;

  useEffect(() => {
    if (autoInit) void init();
  }, [autoInit, init]);
  useEffect(() => {
    if (autoInit && visibleSession) void recoverChildren(visibleSession);
  }, [autoInit, visibleSession, currentProject, recoverChildren]);

  const runs = useMemo<SubAgentRun[]>(
    () => visibleSession ? runsForSession({ runs: runsMap, subscribed: true, error: null } as SubAgentState, visibleSession) : [],
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
        <div className="flex items-center gap-2">
        {visibleSession && <button type="button" onClick={() => void recoverChildren(visibleSession)}
          className="text-[11px] text-minimax-muted hover:text-minimax-fg">刷新子任务</button>}
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
      </div>
      {recoveryError && <div role="alert" className="mb-2 text-[11px] text-status-error">子任务恢复失败：{recoveryError}。请刷新后核对状态。</div>}
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
        {!isFinished(run.status) && run.status !== "unknown" && !run.recovery_service_id && !run.admission_unknown && (
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
          {run.parent_run_id && <div>父任务：<span className="font-mono">{run.parent_run_id}</span></div>}
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
          {run.text_truncated && <div>恢复的结果文本已截断。</div>}
        </div>
      )}
    </li>
  );
}

function validReceipt(value: ChildWorkspaceReceipt | undefined, runId: string): value is ChildWorkspaceReceipt {
  return !!value && value.run_id === runId && typeof value.complete === "boolean" && typeof value.pruned === "boolean" &&
    Array.isArray(value.merged) && value.merged.length <= 500 && value.merged.every((item) => item && typeof item.path === "string" && typeof item.sha256 === "string") &&
    Array.isArray(value.noop) && value.noop.length <= 500 && value.noop.every((item) => typeof item === "string") &&
    Array.isArray(value.conflicts) && value.conflicts.length <= 500 && value.conflicts.every((item) => item && typeof item.path === "string") &&
    (!value.complete || value.conflicts.length === 0);
}

export function ChildWorkspaceReview({ runId, sessionId, teamRunId, allowCollect = true }: { runId: string; sessionId: string; teamRunId?: string; allowCollect?: boolean }): JSX.Element {
  const [review, setReview] = useState<ReviewResult | null>(null);
  const [receipt, setReceipt] = useState<{ report: ChildWorkspaceReceipt; root: string; service: string; kind: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState<{ id: string; status: string; result?: Record<string, unknown> | null } | null>(null);
  const validationKey = useRef<string | null>(null);
  const generation = useRef(0);
  const projectId = useSessionStore((s) => s.sessions.find((s) => s.id === sessionId)?.project_id ?? null);
  useEffect(() => {
    setReview(null); setReceipt(null); setMessage(""); setBusy(false); setValidation(null); validationKey.current = null;
    const epoch = generation;
    return () => { epoch.current++; };
  }, [runId, sessionId, projectId, teamRunId]);
  useEffect(() => ipc.onSideCar(() => { ++generation.current; setReview(null); setReceipt(null); setValidation(null); validationKey.current = null; setBusy(false); setMessage("连接变化，请重新查看子工作区和收集结果。"); }), []);
  useEffect(() => {
    let cancelled = false;
    const host = ipc.agentBaseUrl;
    void ipc.request<{ runs: Array<{ id: string; session_id: string; status: string; metadata?: Record<string, unknown> }> }>("run.list", { session_id: sessionId, limit: 100 }).then(({ runs }) => {
      if (cancelled || host !== ipc.agentBaseUrl || useSessionStore.getState().currentSessionId !== sessionId) return;
      const latest = runs.find((run) => run.session_id === sessionId && run.metadata?.manual_team_validation === true &&
        (run.metadata?.team_run_id ?? null) === (teamRunId ?? null) && run.metadata?.child_run_id === runId);
      if (latest) setValidation({ id: latest.id, status: latest.status });
    }).catch(() => { /* History is optional; an absent result is not a pass. */ });
    return () => { cancelled = true; };
  }, [runId, sessionId, teamRunId, projectId]);
  const invoke = async (collect: boolean, skipConflicts = false) => {
    const ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox"));
    setBusy(true); setMessage("");
    try {
      if (collect && !review) return;
      const interruptedCollection = collect && !allowCollect;
      if (interruptedCollection) {
        if (!review?.team_collection?.eligible || !review.team_collection.step_id) throw new Error("缺少可收集的完成记录或停止证据");
        const accepted = await requestConfirmation({ title: "收集中断团队的已完成成员",
          description: `请先核对团队及相关进程确已停止。受管任务已收束并不证明外逸进程不存在。将按已审阅版本收集到 ${review.root}；冲突和删除不会自动覆盖，异常可能造成部分写入。`,
          confirmLabel: "确认已停止并收集" });
        if (!accepted || !current()) return;
      }
      const result = await ipc.request<ReviewResult & { report?: ChildWorkspaceReceipt }>(
        "run.child_workspace", { run_id: runId, session_id: sessionId, action: collect ? "collect" : "preview",
          ...(teamRunId ? { team_run_id: teamRunId } : {}),
          ...(interruptedCollection && review?.team_collection?.step_id ? { confirmed_team_stopped: true, expected_team_step_id: review.team_collection.step_id } : {}),
          ...(collect && review ? { revision: review.revision, expected_service_id: review.service_id, on_conflict: skipConflicts ? "skip" : "fail" } : {}) },
      );
      if (!current()) return;
      if (result.session_id !== sessionId || result.project_id !== projectId || !result.service_id ||
          (collect && result.service_id !== review?.service_id) || (!collect && result.run_id !== runId)) throw new Error("会话、项目或服务已变化，请重新查看");
      if (collect) {
        if (!validReceipt(result.report, runId)) throw new Error("收集回执无效，请重新查看并核对文件");
        setReceipt({ report: result.report, root: review!.root, service: result.service_id, kind: review!.kind });
        setReview(null);
        setMessage(result.report.complete ? "收集已完成，尚未验证。子工作区及备份仍保留。" : "收集未完成；请核对冲突与已写入文件。");
      } else {
        if (result.receipt && !validReceipt(result.receipt, runId)) throw new Error("已保存的收集回执无效");
        setReview(result);
        setReceipt(result.receipt ? { report: result.receipt, root: result.root, service: result.service_id, kind: result.kind } : null);
      }
    } catch (error) {
      if (current()) {
        setReview(null); setReceipt(null);
        setMessage(`${error instanceof Error ? error.message : String(error)}${collect ? "。结果未确认，可能部分收集；请重新查看并逐项核对父目录，不能推断哪些文件已写入。" : ""}`);
      }
    } finally { if (current()) setBusy(false); }
  };
  const addFollowup = async () => {
    if (!receipt?.report.complete || !projectId || busy) return;
    const expected = receipt, ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === projectId;
    setBusy(true); setMessage("");
    try {
      const result = await ipc.request<ReviewResult>("run.child_workspace", { run_id: runId, session_id: sessionId, action: "preview", ...(teamRunId ? { team_run_id: teamRunId } : {}) });
      if (!current()) return;
      if (result.run_id !== runId || result.session_id !== sessionId || result.project_id !== projectId || result.service_id !== expected.service ||
          result.root !== expected.root || result.state !== "collected" || !validReceipt(result.receipt, runId) ||
          JSON.stringify(result.receipt) !== JSON.stringify(expected.report)) throw new Error("收集回执或工作区绑定已变化，请重新查看");
      const merged = result.receipt.merged, noop = result.receipt.noop;
      const lines = [`子任务 ${runId} 的工作区收集回执（未执行验证）：`, `父目录：${result.root}`, `工作区类型：${result.kind}`,
        `已复制文件：${merged.length} 项`, ...merged.slice(0, 30).map((item) => `- ${item.path}`),
        ...(merged.length > 30 ? [`另有 ${merged.length - 30} 项未列出。`] : []),
        `父目录原已相同：${noop.length} 项`, ...noop.slice(0, 20).map((path) => `- ${path}`),
        ...(noop.length > 20 ? [`另有 ${noop.length - 20} 项未列出。`] : []),
        "此回执不证明文件此刻未变化，也不代表验证或验收通过。请先核对当前文件，再按当前任务权限和项目配置决定验证步骤。",
        "后续目标：（请填写）"];
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text: lines.join("\n") };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开；请先切换到聊天再试。");
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  const startValidation = async () => {
    if (!receipt?.report.complete || busy) return;
    const ticket = ++generation.current, host = ipc.agentBaseUrl, expected = receipt;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === projectId;
    setBusy(true); setMessage("");
    try {
      const snapshot = await ipc.request<ReviewResult & { validate_command?: string | null; parent_run_id?: string | null }>("run.child_workspace", {
        run_id: runId, session_id: sessionId, ...(teamRunId ? { team_run_id: teamRunId } : {}), action: "preview",
      });
      if (!current()) return;
      if (snapshot.service_id !== expected.service || snapshot.session_id !== sessionId || snapshot.project_id !== projectId ||
          snapshot.root !== expected.root || snapshot.state !== "collected" || !snapshot.validate_command || (!teamRunId && !snapshot.parent_run_id) ||
          !validReceipt(snapshot.receipt, runId) || JSON.stringify(snapshot.receipt) !== JSON.stringify(expected.report)) {
        throw new Error("收集回执、项目或验证命令不可用，请重新查看并配置项目验证命令。");
      }
      const accepted = await requestConfirmation({ title: "运行项目验证命令", confirmLabel: "运行验证",
        description: `将在 ${snapshot.root} 执行：\n${snapshot.validate_command}\n该命令可修改文件或访问网络。结果只代表此次命令，不代表验收。` });
      if (!accepted || !current()) return;
      validationKey.current ??= crypto.randomUUID();
      const started = await ipc.request<{ run: { id: string; status: string }; service_id: string; session_id: string; project_id: string }>("run.verify_collected", {
        session_id: sessionId, ...(teamRunId ? { team_run_id: teamRunId } : { parent_run_id: snapshot.parent_run_id }), child_run_id: runId, expected_service_id: expected.service,
        revision: snapshot.revision, expected_command: snapshot.validate_command, idempotency_key: validationKey.current, confirmed: true,
      });
      if (!current()) return;
      if (started.service_id !== expected.service || started.session_id !== sessionId || started.project_id !== projectId) throw new Error("验证绑定已变化，请查看任务列表。");
      setValidation({ id: started.run.id, status: started.run.status });
      setMessage("验证已接单；可刷新结果或请求停止。网络异常时请先查任务列表，避免重复执行。");
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  const refreshValidation = async (stop: boolean) => {
    if (!validation || busy) return;
    const ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === sessionId;
    setBusy(true);
    try {
      if (stop) await ipc.request("run.cancel", { run_id: validation.id });
      const data = await ipc.request<{ run: { id: string; session_id: string; status: string; metadata?: Record<string, unknown> }; result?: Record<string, unknown> | null }>("run.get", { run_id: validation.id });
      if (!current()) return;
      if (data.run.session_id !== sessionId || (data.run.metadata?.team_run_id ?? null) !== (teamRunId ?? null) || data.run.metadata?.child_run_id !== runId) throw new Error("验证运行绑定已变化");
      setValidation({ id: data.run.id, status: data.run.status, result: data.result });
      if (stop) setMessage("已请求停止；请刷新以确认最终状态。");
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  return <div className="space-y-2 border-t border-line pt-2 text-xs">
    <button type="button" disabled={busy} onClick={() => void invoke(false)}>查看子任务工作区结果</button>
    {message && <p role="status" className="break-words text-ink-1">{message}</p>}
    {receipt && <div className="space-y-1 rounded border border-line bg-surface-1 p-2">
      <p className="font-medium">{receipt.report.complete ? "收集回执 · 已完成" : "收集回执 · 未完成"}</p>
      <p>已复制 {receipt.report.merged.length} 项；父目录原已相同 {receipt.report.noop.length} 项；冲突 {receipt.report.conflicts.length} 项。尚未验证或验收。</p>
      {!!receipt.report.merged.length && <details><summary>已复制文件</summary><ul>{receipt.report.merged.map((item) => <li key={item.path} className="break-all">{item.path}</li>)}</ul></details>}
      {!!receipt.report.noop.length && <details><summary>父目录原已相同的文件</summary><ul>{receipt.report.noop.map((path) => <li key={path} className="break-all">{path}</li>)}</ul></details>}
      {!!receipt.report.conflicts.length && <details open><summary>未处理冲突</summary><ul>{receipt.report.conflicts.map((item) => <li key={item.path} className="break-all">{item.path} · {item.reason || "父目录已变化"}</li>)}</ul></details>}
      <p>回执记录当时结果，文件可能后来发生变化。请核对父目录并通过后续任务按权限执行需要的验证。</p>
      {receipt.report.complete && <button type="button" disabled={busy || !projectId} onClick={() => void addFollowup()}>将收集回执加入后续任务草稿</button>}
      {allowCollect && receipt.report.complete && <button type="button" disabled={busy || !projectId || !!validation} onClick={() => void startValidation()}>审阅并运行项目验证</button>}
      {validation && <div className="rounded border border-line p-2">
        <p>验证运行 {validation.id} · {validation.status}</p>
        {validation.result ? <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(validation.result.verification ?? validation.result, null, 2)}</pre> : <p>结果尚未读取或仍未知。</p>}
        <button type="button" disabled={busy} onClick={() => void refreshValidation(false)}>刷新结果</button>{" "}
        {["queued", "running", "planning"].includes(validation.status) && <button type="button" disabled={busy} onClick={() => void refreshValidation(true)}>停止验证</button>}
      </div>}
    </div>}
    {review && <div className="space-y-1">
      <p>父目录：{review.root} · {review.kind} · {review.state}</p>
      <p>对比当前父目录与子任务结果；同期用户修改不归因于 Agent。普通独立目录不是操作系统沙盒。</p>
      {!!review.collection_attempts?.length && <details open><summary>收集历史（最近 50 次）</summary>
        {review.collection_attempts.map((attempt) => <div key={attempt.id} className="my-1 rounded border border-line p-2">
          <p>{attempt.id} · {attempt.status === "complete" ? "完成" : attempt.status === "partial" ? "部分收集" : "中断或进行中，结果未确认"}</p>
          {validReceipt(attempt.report, runId) ? <>
            <p>已确认复制 {attempt.report.merged.length} 项；当时已相同 {attempt.report.noop.length} 项；冲突 {attempt.report.conflicts.length} 项。</p>
            {!!attempt.report.merged.length && <details><summary>已确认复制</summary><ul>{attempt.report.merged.map((item) => <li key={item.path} className="break-all">{item.path}</li>)}</ul></details>}
            {!!attempt.report.noop.length && <details><summary>当时已相同</summary><ul>{attempt.report.noop.map((path) => <li key={path} className="break-all">{path}</li>)}</ul></details>}
            {!!attempt.report.conflicts.length && <details><summary>当时的冲突</summary><ul>{attempt.report.conflicts.map((item) => <li key={item.path} className="break-all">{item.path} · {item.reason || "父目录已变化"}</li>)}</ul></details>}
          </> : <p>记录格式不可用，请手动核对。</p>}
          {attempt.pending_path && <p className="break-all">复制结果待核对：{attempt.pending_path}。不能推断是否已写入。</p>}
        </div>)}
        <p>历史记录反映当时的结果，不能证明当前文件未变化，也不代表验证或验收。</p>
      </details>}
      {review.omitted.length > 0 && <p>未纳入：{review.omitted.join("、")}</p>}
      {review.files.length === 0 && <p>没有记录到文件变化。</p>}
      {review.files.map((file) => <details key={file.path}><summary>{file.path}{file.conflict ? ` · 冲突：${file.conflict}` : ""}</summary>
        {file.diff_unavailable ? <p>差异过大或非 UTF-8，请手动查看。</p> : <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{file.diff || "（内容相同或无文本差异）"}</pre>}
        {file.diff_truncated && <p>差异已截断，请手动查看。</p>}
      </details>)}
      <button type="button" disabled={(!allowCollect && !review.team_collection?.eligible) || busy || review.state === "collected" || review.files.some((f) => !!f.conflict)} onClick={() => void invoke(true)}>收集已审阅的无冲突变化</button>
      {review.files.some((f) => !!f.conflict) && review.files.some((f) => !f.conflict && f.parent_sha256 !== f.child_sha256) && <button type="button" disabled={(!allowCollect && !review.team_collection?.eligible) || busy || review.state === "collected"} onClick={() => void invoke(true, true)}>仅收集无冲突文件，跳过冲突</button>}
      {!allowCollect && <p>{review.team_collection?.eligible ? "已保存成员完成及受管任务收束记录；请自行核对相关进程并明确确认。" : `不能收集：${review.team_collection?.reason || "缺少停止或成员完成证据"}。`} 工作副本不是操作系统沙箱。</p>}
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
