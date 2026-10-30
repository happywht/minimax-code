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
import { ChildConflictHandoff } from "./ChildConflictHandoff";
import { ChildResultsHandoff } from "./ChildResultsHandoff";

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
      {visibleSession && <ChildResultsHandoff sessionId={visibleSession} />}
      {visibleSession && <ChildHistory sessionId={visibleSession} />}
      {visibleSession && <OlderChildLookup sessionId={visibleSession} />}
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

type HistoricChild = { run_id: string; parent_run_id: string; agent_name: string; status: string; live: boolean;
  prompt: string; text: string; text_truncated: boolean; partial: boolean; error: string | null };
type ChildCursor = { created_at: string; run_id: string };
function ChildHistory({ sessionId }: { sessionId: string }): JSX.Element {
  const session = useSessionStore((s) => s.currentSessionId), selectedProject = useSessionStore((s) => s.currentProjectId);
  const project = useSessionStore((s) => s.sessions.find((item) => item.id === sessionId)?.project_id ?? null);
  const [rows, setRows] = useState<HistoricChild[]>([]), [cursor, setCursor] = useState<ChildCursor | null>(null);
  const [service, setService] = useState(""), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [handoff, setHandoff] = useState<{ row: HistoricChild; text: string } | null>(null);
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setRows([]); setCursor(null); setService(""); setHandoff(null); setBusy(false); setMessage(""); return () => { ++epoch.current; }; }, [sessionId, session, project, selectedProject]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setRows([]); setCursor(null); setService(""); setHandoff(null); setBusy(false); setMessage("连接变化，请重新读取历史。"); }), []);
  const load = async (more: boolean) => {
    if (lock.current || session !== sessionId || (selectedProject !== project && !(project === null && selectedProject === "inbox")) || (more && (!service || !cursor))) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, prior = more ? cursor : null;
    const current = () => ticket === epoch.current && host === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === sessionId &&
      useSessionStore.getState().currentProjectId === selectedProject && (useSessionStore.getState().sessions.find((s) => s.id === sessionId)?.project_id ?? null) === project;
    lock.current = true; setBusy(true); setMessage("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!identity.service_id || (more && identity.service_id !== service)) throw new Error("服务身份变化，请重新读取历史。");
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string; children: HistoricChild[]; next_cursor: ChildCursor | null }>("run.child_list", {
        session_id: sessionId, history: true, expected_service_id: identity.service_id, expected_project_id: project,
        ...(prior ? { before_created_at: prior.created_at, before_run_id: prior.run_id } : {}),
      });
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== after.service_id || result.service_id !== identity.service_id || result.session_id !== sessionId || result.project_id !== project ||
          !Array.isArray(result.children) || result.children.some((item) => !item.run_id || !item.parent_run_id) ||
          (result.next_cursor && (!result.next_cursor.run_id || !result.next_cursor.created_at))) throw new Error("历史归属或游标不匹配。");
      setRows((old) => more ? [...old, ...result.children.filter((item) => !old.some((previous) => previous.run_id === item.run_id))] : result.children);
      setCursor(result.next_cursor); setService(identity.service_id);
      if (!result.children.length) setMessage("没有更多可确认归属的直接子任务。");
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  const append = async () => {
    if (!handoff || !service || !project || lock.current || !handoff.text.trim()) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, saved = handoff;
    const current = () => ticket === epoch.current && host === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === sessionId &&
      useSessionStore.getState().currentProjectId === project && (useSessionStore.getState().sessions.find((s) => s.id === sessionId)?.project_id ?? null) === project;
    lock.current = true; setBusy(true); setMessage("");
    try {
      const before = await typedIPC.getServiceIdentity();
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string; children: HistoricChild[] }>("run.child_list", { session_id: sessionId, run_id: saved.row.run_id });
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const fresh = result.children?.[0];
      if (before.service_id !== service || after.service_id !== service || result.service_id !== service || result.session_id !== sessionId || result.project_id !== project ||
          result.children?.length !== 1 || !fresh || JSON.stringify(fresh) !== JSON.stringify(saved.row) ||
          !["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(fresh.status) || fresh.live)
        throw new Error("子任务记录或归属已变化，请刷新并重新审阅。");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session: sessionId, text: saved.text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "聊天输入区未打开，请切换到聊天。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") setHandoff(null);
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  return <details className="mb-2 rounded border border-line p-2 text-xs"><summary>浏览子任务历史</summary>
    <p>按创建时间倒序，每页 50 条；活动但无法确认仍在运行的记录需要核对。历史可能随新记录或删除变化。</p>
    <button type="button" disabled={busy} onClick={() => void load(false)}>读取／刷新历史</button>
    <ul>{rows.map((item) => <li key={item.run_id}><details><summary className="break-all">{item.agent_name || "子 Agent"} · {item.run_id} · {item.status}{!["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(item.status) && !item.live ? " · 状态待核对" : ""}</summary>
      <p className="break-all">父运行：{item.parent_run_id}{item.partial ? " · 部分结果" : ""}</p><p>任务：{item.prompt}</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{item.text || "（没有已保存的结果文本）"}</pre>{item.text_truncated && <p>结果已截断。</p>}{item.error && <p>{item.error}</p>}
      {["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(item.status) && !item.live && <button type="button" disabled={busy || !project} onClick={() => setHandoff({ row: item, text: `子任务结果交接（请审阅并编辑）：\n子任务：${item.agent_name || "子 Agent"} · ${item.run_id}\n父运行：${item.parent_run_id} · 状态：${item.status}${item.partial ? " · 部分结果" : ""}\n任务：${item.prompt.slice(0, 1000)}\n结果：\n${item.text.slice(0, 2000)}${item.text_truncated || item.text.length > 2000 ? "\n（结果已截断）" : ""}${item.error ? `\n错误：${item.error.slice(0, 1000)}` : ""}\n\n以上为已保存的子任务文本，不代表文件已合并或验证通过。请核对实际文件与验证记录，保留用户同期修改。\n后续目标：` })}>编辑此结果交接</button>}</details></li>)}</ul>
    {handoff && <div className="space-y-1"><label className="block">可编辑的结果交接<textarea aria-label="历史子任务结果交接" rows={8} maxLength={7500} disabled={busy} value={handoff.text} onChange={(event) => setHandoff({ ...handoff, text: event.target.value })} className="w-full rounded border border-line bg-surface-0 p-1" /></label>
      <button type="button" disabled={busy || !handoff.text.trim()} onClick={() => void append()}>重新核对并加入草稿</button>{" "}<button type="button" disabled={busy} onClick={() => setHandoff(null)}>取消</button>
      <p>文本可能含敏感信息；加入后仍须手动发送。不会合并文件或执行验证。</p></div>}
    {cursor && <button type="button" disabled={busy} onClick={() => void load(true)}>加载更多</button>}
    {message && <p role="status">{message}</p>}
  </details>;
}

function OlderChildLookup({ sessionId }: { sessionId: string }): JSX.Element {
  const project = useSessionStore((s) => s.sessions.find((item) => item.id === sessionId)?.project_id ?? null);
  const selected = useSessionStore((s) => s.currentSessionId);
  const selectedProject = useSessionStore((s) => s.currentProjectId);
  const [id, setId] = useState(""), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [child, setChild] = useState<{ run_id: string; parent_run_id: string; agent_name: string; status: string; live: boolean; prompt: string; text: string; text_truncated: boolean; partial: boolean; error: string | null } | null>(null);
  const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setChild(null); setMessage(""); setBusy(false); return () => { ++epoch.current; }; }, [sessionId, project, selected, selectedProject]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setChild(null); setBusy(false); setMessage("连接变化，请重新查询。"); }), []);
  const lookup = async () => {
    const run = id.trim(); if (!run || busy || selected !== sessionId) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && host === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().sessions.find((s) => s.id === sessionId)?.project_id ?? null) === project &&
      useSessionStore.getState().currentProjectId === selectedProject && (selectedProject === project || (project === null && selectedProject === "inbox"));
    setBusy(true); setChild(null); setMessage("");
    try {
      const before = await typedIPC.getServiceIdentity();
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string; children: Array<NonNullable<typeof child>> }>("run.child_list", { session_id: sessionId, run_id: run });
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (!before.service_id || before.service_id !== after.service_id || result.service_id !== before.service_id ||
          result.session_id !== sessionId || result.project_id !== project || result.children.length > 1 ||
          result.children.some((item) => item.run_id !== run || !item.parent_run_id)) throw new Error("服务或子任务归属无法确认。");
      if (!result.children.length) { setMessage("未找到当前会话可确认归属的直接子任务。"); return; }
      setChild(result.children[0]);
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  return <details className="mb-2 rounded border border-line p-2 text-xs"><summary>按运行 ID 查看较早子任务</summary>
    <p>仅查询当前会话有持久父运行绑定的直接子任务；不会重新执行。</p>
    <input aria-label="子任务运行 ID" value={id} maxLength={128} disabled={busy} onChange={(event) => { setId(event.target.value); setChild(null); }} className="w-full rounded border border-line bg-surface-0 p-1" />
    <button type="button" disabled={busy || !id.trim() || selected !== sessionId} onClick={() => void lookup()}>查询</button>
    {message && <p role="status">{message}</p>}
    {child && <div className="break-all"><p>{child.agent_name || "子 Agent"} · {child.run_id} · 父运行 {child.parent_run_id}</p>
      <p>持久状态：{child.status}{!["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(child.status) && !child.live ? " · 当前运行状态待核对" : ""}{child.partial ? " · 部分结果" : ""}</p>
      <p>任务：{child.prompt}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap">{child.text || "（没有已保存的结果文本）"}</pre>
      {child.text_truncated && <p>结果已截断。</p>}{child.error && <p>错误：{child.error}</p>}</div>}
  </details>;
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
    (value.remaining === undefined || (Array.isArray(value.remaining) && value.remaining.length <= 500 && value.remaining.every((path) => typeof path === "string"))) &&
    Array.isArray(value.merged) && value.merged.length <= 500 && value.merged.every((item) => item && typeof item.path === "string" && typeof item.sha256 === "string") &&
    Array.isArray(value.noop) && value.noop.length <= 500 && value.noop.every((item) => typeof item === "string") &&
    Array.isArray(value.conflicts) && value.conflicts.length <= 500 && value.conflicts.every((item) => item && typeof item.path === "string") &&
    (!value.complete || value.conflicts.length === 0);
}

export function ChildWorkspaceReview({ runId, sessionId, teamRunId, allowCollect = true }: { runId: string; sessionId: string; teamRunId?: string; allowCollect?: boolean }): JSX.Element {
  const [review, setReview] = useState<ReviewResult | null>(null);
  const [receipt, setReceipt] = useState<{ report: ChildWorkspaceReceipt; root: string; service: string; kind: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [merge, setMerge] = useState<{ path: string; text: string } | null>(null);
  const [backup, setBackup] = useState<{ attempt_id: string; path: string; sha256: string; content: string } | null>(null);
  const [backupDestination, setBackupDestination] = useState("");
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState<{ id: string; status: string; result?: Record<string, unknown> | null } | null>(null);
  const validationKey = useRef<string | null>(null);
  const generation = useRef(0);
  const projectId = useSessionStore((s) => s.sessions.find((s) => s.id === sessionId)?.project_id ?? null);
  useEffect(() => {
    setReview(null); setReceipt(null); setMerge(null); setBackup(null); setMessage(""); setBusy(false); setValidation(null); validationKey.current = null;
    const epoch = generation;
    return () => { epoch.current++; };
  }, [runId, sessionId, projectId, teamRunId]);
  useEffect(() => ipc.onSideCar(() => { ++generation.current; setReview(null); setReceipt(null); setMerge(null); setBackup(null); setValidation(null); validationKey.current = null; setBusy(false); setMessage("连接变化，请重新查看子工作区和收集结果。"); }), []);
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
  const invoke = async (collect: boolean, skipConflicts = false, selectedPath?: string) => {
    const ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox"));
    setBusy(true); setMessage("");
    try {
      if (collect && !review) return;
      if (collect && selectedPath) {
        const accepted = await requestConfirmation({ title: `收集文件 ${selectedPath}`,
          description: "仅收集此文件；父文件变化会阻止覆盖。其他文件和冲突保留待审阅，异常时请核对实际文件。",
          confirmLabel: "收集此文件" });
        if (!accepted || !current()) return;
      }
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
          ...(collect && selectedPath ? { path: selectedPath } : {}),
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
        setMessage(result.report.complete ? "收集已完成，尚未验证。子工作区及备份仍保留。" : "已处理所选变更；其他变更或冲突仍待审阅，请核对已写入文件。");
      } else {
        if (result.receipt && !validReceipt(result.receipt, runId)) throw new Error("已保存的收集回执无效");
        setMerge(null); setBackup(null); setReview(result);
        setReceipt(result.receipt ? { report: result.receipt, root: result.root, service: result.service_id, kind: result.kind } : null);
      }
    } catch (error) {
      if (current()) {
        setReview(null); setReceipt(null);
        setMessage(`${error instanceof Error ? error.message : String(error)}${collect ? "。结果未确认，可能部分收集；请重新查看并逐项核对父目录，不能推断哪些文件已写入。" : ""}`);
      }
    } finally { if (current()) setBusy(false); }
  };
  const readBackup = async (attemptId: string, path: string, sha256: string) => {
    if (!review || busy) return;
    const snapshot = review, ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox"));
    setBusy(true); setMessage(""); setBackup(null);
    try {
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string;
        backup: { attempt_id: string; path: string; sha256: string; content: string } }>("run.child_workspace", {
        run_id: runId, session_id: sessionId, ...(teamRunId ? { team_run_id: teamRunId } : {}), action: "read_backup",
        revision: snapshot.revision, expected_service_id: snapshot.service_id, attempt_id: attemptId, path,
      });
      if (!current()) return;
      if (result.session_id !== sessionId || result.project_id !== projectId || result.service_id !== snapshot.service_id ||
          result.backup?.attempt_id !== attemptId || result.backup.path !== path || result.backup.sha256 !== sha256 ||
          typeof result.backup.content !== "string") throw new Error("备份绑定或摘要不匹配，请重新查看");
      setBackup(result.backup); setBackupDestination("");
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  const restoreBackupCopy = async () => {
    if (!review || !backup || !backupDestination.trim() || busy || (!allowCollect && !review.team_collection?.eligible)) return;
    const snapshot = review, saved = backup, destination = backupDestination.trim();
    const ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox"));
    setBusy(true); setMessage("");
    try {
      const interrupted = !allowCollect;
      if (interrupted && !snapshot.team_collection?.step_id) throw new Error("缺少成员完成记录或停止证据");
      const accepted = await requestConfirmation({ title: "将备份恢复为新文件", confirmLabel: "确认创建",
        description: `${interrupted ? "请先核对团队及相关进程确已停止。" : ""}将在父目录创建 ${destination}，内容来自 ${saved.path} 的合并前备份。目标必须不存在，父目录须已存在。` });
      if (!accepted || !current()) return;
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string;
        backup_copy: { attempt_id: string; source_path: string; destination: string; sha256: string } }>("run.child_workspace", {
        run_id: runId, session_id: sessionId, ...(teamRunId ? { team_run_id: teamRunId } : {}),
        action: "restore_backup_copy", attempt_id: saved.attempt_id, path: saved.path, destination, confirmed_backup_copy: true,
        revision: snapshot.revision, expected_service_id: snapshot.service_id,
        ...(interrupted ? { confirmed_team_stopped: true, expected_team_step_id: snapshot.team_collection!.step_id } : {}),
      });
      if (!current()) return;
      if (result.session_id !== sessionId || result.project_id !== projectId || result.service_id !== snapshot.service_id ||
          result.backup_copy?.attempt_id !== saved.attempt_id || result.backup_copy.source_path !== saved.path ||
          result.backup_copy.destination !== destination || result.backup_copy.sha256 !== saved.sha256) throw new Error("备份副本回执不匹配");
      setBackup(null); setReview(null); setBackupDestination("");
      setMessage(`已创建备份副本 ${destination}；原文件未被覆盖。`);
    } catch (error) {
      if (current()) { setBackup(null); setReview(null); setMessage(`创建结果未确认，请先检查目标文件：${error instanceof Error ? error.message : String(error)}`); }
    } finally { if (current()) setBusy(false); }
  };
  const resolveText = async () => {
    if (!review || !merge || busy || (!allowCollect && !review.team_collection?.eligible)) return;
    const snapshot = review, draft = merge, ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && host === ipc.agentBaseUrl &&
      useSessionStore.getState().currentSessionId === sessionId &&
      (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox"));
    setBusy(true); setMessage("");
    try {
      const interruptedResolution = !allowCollect;
      if (interruptedResolution && !snapshot.team_collection?.step_id) throw new Error("缺少成员完成记录或停止证据");
      const accepted = await requestConfirmation({ title: "写入手动合并结果", confirmLabel: "确认写入",
        description: `${interruptedResolution ? "请先核对团队及相关进程确已停止。受管任务已收束并不证明外逸进程不存在。" : ""}将用编辑框的完整内容替换父目录文件 ${snapshot.root} / ${draft.path}。请先核对两侧差异和编辑内容；系统会保留写入前备份与子工作区，并检查审阅版本。` });
      if (!accepted || !current()) return;
      const result = await ipc.request<{ session_id: string; project_id: string | null; service_id: string; resolution: { path: string; backup: string } }>("run.child_workspace", {
        run_id: runId, session_id: sessionId, ...(teamRunId ? { team_run_id: teamRunId } : {}), action: "resolve_text",
        ...(interruptedResolution ? { confirmed_team_stopped: true, expected_team_step_id: snapshot.team_collection!.step_id } : {}),
        revision: snapshot.revision, expected_service_id: snapshot.service_id, path: draft.path, content: draft.text,
      });
      if (!current()) return;
      if (result.session_id !== sessionId || result.project_id !== projectId || result.service_id !== snapshot.service_id || result.resolution?.path !== draft.path) throw new Error("写入结果绑定无法确认");
      setMerge(null); setReview(null); setReceipt(null);
      setMessage(`已写入手动合并内容，备份：${result.resolution.backup}。请重新查看并收集；尚未验证或验收。`);
    } catch (error) {
      if (current()) { setMerge(null); setReview(null); setReceipt(null); setMessage(`${error instanceof Error ? error.message : String(error)}。写入结果可能未确认，请重新查看历史并核对父文件与备份。`); }
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
  const addValidationFollowup = async () => {
    if (!validation?.result || !receipt || !projectId || busy) return;
    const saved = validation, expected = receipt, ticket = ++generation.current, host = ipc.agentBaseUrl;
    const current = () => ticket === generation.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === projectId;
    setBusy(true); setMessage("");
    try {
      const before = await ipc.request<{ service_id: string }>("runtime.service_identity", {});
      const fresh = await ipc.request<{ run: { id: string; session_id: string; status: string; metadata?: Record<string, unknown> }; result?: Record<string, unknown> | null }>("run.get", { run_id: saved.id });
      const after = await ipc.request<{ service_id: string }>("runtime.service_identity", {});
      if (!current()) return;
      const md = fresh.run.metadata;
      if (before.service_id !== expected.service || after.service_id !== expected.service || fresh.run.id !== saved.id ||
          fresh.run.session_id !== sessionId || md?.project_id !== projectId || md?.workspace_snapshot !== expected.root ||
          md?.manual_team_validation !== true || (md?.team_run_id ?? null) !== (teamRunId ?? null) || md?.child_run_id !== runId ||
          fresh.run.status !== saved.status || JSON.stringify(fresh.result) !== JSON.stringify(saved.result))
        throw new Error("验证记录或服务归属已变化，请刷新并重新审阅。");
      const evidence = JSON.stringify(fresh.result?.verification ?? fresh.result, null, 2);
      const excerpt = evidence.slice(0, 4500);
      const detail: { host: string; project: string; session: string; text: string; result?: string } = {
        host, project: projectId, session: sessionId,
        text: `子任务收集后的项目验证记录（请审阅后编辑）：\n子任务：${runId}${teamRunId ? ` · 团队：${teamRunId}` : ""}\n验证运行：${saved.id} · 状态：${saved.status}\n父目录：${expected.root}\n\n${excerpt}${evidence.length > excerpt.length ? "\n（记录已截断，请查看原运行）" : ""}\n\n这只反映当次命令的已保存结果，不代表当前文件未变化或验收通过；请核对输出与未确认事项。`,
      };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "聊天输入区未打开，请先打开当前会话。");
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
      {!!receipt.report.remaining?.length && <details open><summary>未选择的变更</summary><ul>{receipt.report.remaining.map((path) => <li key={path} className="break-all">{path}</li>)}</ul></details>}
      <p>回执记录当时结果，文件可能后来发生变化。请核对父目录并通过后续任务按权限执行需要的验证。</p>
      {receipt.report.complete && <button type="button" disabled={busy || !projectId} onClick={() => void addFollowup()}>将收集回执加入后续任务草稿</button>}
      {allowCollect && receipt.report.complete && <button type="button" disabled={busy || !projectId || !!validation} onClick={() => void startValidation()}>审阅并运行项目验证</button>}
      {validation && <div className="rounded border border-line p-2">
        <p>验证运行 {validation.id} · {validation.status}</p>
        {validation.result ? <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(validation.result.verification ?? validation.result, null, 2)}</pre> : <p>结果尚未读取或仍未知。</p>}
        <button type="button" disabled={busy} onClick={() => void refreshValidation(false)}>刷新结果</button>{" "}
        {validation.result && <button type="button" disabled={busy || !projectId} onClick={() => void addValidationFollowup()}>将验证记录加入后续草稿</button>}{" "}
        {["queued", "running", "planning"].includes(validation.status) && <button type="button" disabled={busy} onClick={() => void refreshValidation(true)}>停止验证</button>}
      </div>}
    </div>}
    {review && <div className="space-y-1">
      <p>父目录：{review.root} · {review.kind} · {review.state}</p>
      {review.recovery_snapshot && <p className="break-all">中断后的只读快照：{review.child_path}。进程及副作用可能仍未结束，内容可能继续变化；请先核对相关进程，再手动保全和处理文件。此快照不能收集或自动恢复。</p>}
      <p>对比当前父目录与子任务结果；同期用户修改不归因于 Agent。普通独立目录不是操作系统沙盒。</p>
      {!review.recovery_snapshot && !!review.collection_attempts?.length && <details open><summary>收集历史（最近 50 次）</summary>
        {review.collection_attempts.map((attempt) => <div key={attempt.id} className="my-1 rounded border border-line p-2">
          <p>{attempt.operation === "manual_resolution" ? "手动合并 · " : ""}{attempt.id} · {attempt.status === "complete" ? "完成" : attempt.status === "partial" ? "部分收集" : "中断或进行中，结果未确认"}</p>
          {validReceipt(attempt.report, runId) ? <>
            <p>已确认复制 {attempt.report.merged.length} 项；当时已相同 {attempt.report.noop.length} 项；冲突 {attempt.report.conflicts.length} 项。</p>
            {!!attempt.report.merged.length && <details><summary>已确认复制</summary><ul>{attempt.report.merged.map((item) => {
              const baseline = review.files.find((file) => file.path === item.path)?.baseline_sha256;
              return <li key={item.path} className="break-all">{item.path} {baseline && <button type="button" disabled={busy} onClick={() => void readBackup(attempt.id, item.path, baseline)}>查看收集前备份</button>}</li>;
            })}</ul></details>}
            {!!attempt.report.noop.length && <details><summary>当时已相同</summary><ul>{attempt.report.noop.map((path) => <li key={path} className="break-all">{path}</li>)}</ul></details>}
            {!!attempt.report.conflicts.length && <details><summary>当时的冲突</summary><ul>{attempt.report.conflicts.map((item) => <li key={item.path} className="break-all">{item.path} · {item.reason || "父目录已变化"}</li>)}</ul></details>}
          </> : <p>记录格式不可用，请手动核对。</p>}
          {attempt.backup && <p className="break-all">备份路径：{attempt.backup}</p>}
          {attempt.operation === "manual_resolution" && (attempt.path && attempt.parent_sha256 ? <>
            <button type="button" disabled={busy} onClick={() => void readBackup(attempt.id, attempt.path!, attempt.parent_sha256!)}>查看合并前备份</button>
          </> : <p>旧记录缺少原文件摘要，无法在此核验备份；请在文件系统中自行查看。</p>)}
            {backup?.attempt_id === attempt.id && <div>
              <p className="break-all">原文件：{backup.path} · SHA-256：{backup.sha256}</p>
              <p>内容与记录的写入前摘要一致；仅供查看，不会恢复或覆盖文件。</p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{backup.content}</pre>
              {(allowCollect || review.team_collection?.eligible) && <div className="space-y-1">
                <label className="block">恢复到父目录内的新文件（相对路径）<input aria-label="备份副本目标路径" value={backupDestination} disabled={busy} onChange={(event) => setBackupDestination(event.target.value)} className="w-full rounded border border-line bg-surface-0 p-1" /></label>
                <button type="button" disabled={busy || !backupDestination.trim()} onClick={() => void restoreBackupCopy()}>审阅并创建备份副本</button>
              </div>}
              <button type="button" onClick={() => setBackup(null)}>关闭备份内容</button>
            </div>}
          {attempt.pending_path && <p className="break-all">复制结果待核对：{attempt.pending_path}。不能推断是否已写入。</p>}
        </div>)}
        <p>历史记录反映当时的结果，不能证明当前文件未变化，也不代表验证或验收。</p>
      </details>}
      {review.omitted.length > 0 && <p>未纳入：{review.omitted.join("、")}</p>}
      {review.files.length === 0 && <p>没有记录到文件变化。</p>}
      {review.files.map((file) => <details key={file.path}><summary>{file.path}{file.conflict ? ` · 冲突：${file.conflict}` : file.resolved ? " · 已手动合并" : ""}</summary>
        {file.owner_executable !== undefined && <p>新增文件：所有者{file.owner_executable ? "可执行" : "不可执行"}；收集不复制组或其他用户权限。</p>}
        {!review.recovery_snapshot && (allowCollect || review.team_collection?.eligible) && !file.conflict && file.child_sha256 && file.parent_sha256 !== file.child_sha256 && <button type="button" disabled={busy || review.state === "collected"} onClick={() => void invoke(true, false, file.path)}>仅收集此文件</button>}
        {file.diff_unavailable ? <p>差异过大或非 UTF-8，请手动查看。</p> : <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{file.diff || "（内容相同或无文本差异）"}</pre>}
        {file.diff_truncated && <p>差异已截断，请手动查看。</p>}
        {!review.recovery_snapshot && (allowCollect || review.team_collection?.eligible) && file.conflict === "parent_changed" && file.merge_parent_text !== undefined && <button type="button" disabled={busy} onClick={() => setMerge({ path: file.path, text: file.merge_parent_text! })}>编辑手动合并结果</button>}
        {merge?.path === file.path && <div className="space-y-1">
          <p>初始内容为当前父文件；请参考上方差异自行合并子任务成果。提交将写入完整文件。</p>
          {file.merge_child_text !== undefined ? <details><summary>查看完整子任务文件（只读）</summary>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{file.merge_child_text}</pre>
          </details> : <p>完整子任务文件未包含在本次审阅中，请在子工作区自行查看。</p>}
          <textarea aria-label={`合并内容 ${file.path}`} className="min-h-40 w-full rounded border border-line bg-surface-0 p-2 font-mono" value={merge.text} onChange={(event) => setMerge({ ...merge, text: event.target.value })} />
          <button type="button" disabled={busy} onClick={() => void resolveText()}>审阅并写入合并内容</button>{" "}
          <button type="button" disabled={busy} onClick={() => setMerge(null)}>取消编辑</button>
        </div>}
      </details>)}
      {!review.recovery_snapshot && <ChildConflictHandoff review={review} teamRunId={teamRunId} disabled={busy} />}
      {!review.recovery_snapshot && <button type="button" disabled={(!allowCollect && !review.team_collection?.eligible) || busy || review.state === "collected" || review.files.some((f) => !!f.conflict)} onClick={() => void invoke(true)}>收集已审阅的无冲突变化</button>}
      {!review.recovery_snapshot && review.files.some((f) => !!f.conflict) && review.files.some((f) => !f.conflict && f.parent_sha256 !== f.child_sha256) && <button type="button" disabled={(!allowCollect && !review.team_collection?.eligible) || busy || review.state === "collected"} onClick={() => void invoke(true, true)}>仅收集无冲突文件，跳过冲突</button>}
      {!allowCollect && <p>{review.team_collection?.eligible ? "已保存成员完成及受管任务收束记录；请自行核对相关进程并明确确认。" : `不能收集：${review.team_collection?.reason || "缺少停止或成员完成证据"}。`} 工作副本不是操作系统沙箱。</p>}
      {!review.recovery_snapshot && <p>可对小型 UTF-8 文件手动合并；删除及其他冲突需在外部处理。执行中出现错误时可能部分写入，请核对文件。</p>}
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
