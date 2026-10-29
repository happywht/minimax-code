import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { AgentRun, RunStepsResult } from "../../types/ipc";
import { Button } from "../../ui";
import { RunWorkspaceDiff } from "./RunWorkspaceDiff";

type Review = { snapshot: RunStepsResult; service: string; host: string; project: string | null };
type Attempt = { key: string; run: string; session: string; project: string | null; service: string; host: string; cursor: number; budget: number | null; reviewed: true };
const retryStorage = (item: Pick<Attempt, "host" | "service" | "session" | "run">) =>
  `minimax:resume:${encodeURIComponent(item.host)}:${encodeURIComponent(item.service)}:${encodeURIComponent(item.session)}:${encodeURIComponent(item.run)}`;

export function RunRecoveryActions({ run, refresh }: { run: AgentRun; refresh: () => Promise<void> }): JSX.Element | null {
  const [reviewed, setReviewed] = useState(false), [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null), [cancelRequested, setCancelRequested] = useState(false);
  const [resumed, setResumed] = useState(false), [budgetTokens, setBudgetTokens] = useState("");
  const [changeBudget, setChangeBudget] = useState(false);
  const [review, setReview] = useState<Review | null>(null), [attempt, setAttempt] = useState<Attempt | null>(null);
  const [loading, setLoading] = useState(false);
  const epoch = useRef(0), inFlight = useRef(false);
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.sessions.find((item) => item.id === session)?.project_id ?? null);
  const selectedProject = useSessionStore((state) => state.currentProjectId);
  const host = ipc.agentBaseUrl;
  const codex = run.metadata?.engine === "codex";
  const resumable = ["interrupted", "failed", "cancelled", "budget_paused"].includes(run.status);
  const active = ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status);
  useEffect(() => {
    ++epoch.current; setReview(null); setAttempt(null); setReviewed(false); setError(null); setLoading(false); setPending(false); setResumed(false); setCancelRequested(false); setBudgetTokens(""); setChangeBudget(false);
    return () => { ++epoch.current; };
  }, [run.id, run.status, session, project, selectedProject, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setReview(null); setAttempt(null); setReviewed(false); setPending(false); setLoading(false); setError("连接变化，请重新读取运行和待确认请求。"); }), []);
  if (!resumable && !active) return null;
  const hasWorkspace = typeof run.metadata?.workspace_snapshot === "string" && !!run.metadata.workspace_snapshot;
  const missingContext = [
    ...(!hasWorkspace ? ["工作区快照"] : []),
    ...(codex ? (!run.metadata?.codex_thread_id ? ["Codex 线程"] : []) : (!run.metadata?.model_snapshot ? ["模型快照"] : [])),
  ];
  if (!resumable && missingContext.length) return null;
  if (resumable && (run.mode === "team" || run.metadata?.manual_team_validation)) return <p className="mt-2 text-xs text-status-warning">
    此运行不能直接接续。请先核对成员结果、工作区和可能的外部副作用，再明确发起新任务或验证。
  </p>;
  if (resumable && missingContext.length) return <div className="mt-2 space-y-1 text-xs text-status-warning" data-testid="run-recovery-unavailable">
    <p>无法直接接续：记录缺少{missingContext.join("、")}。</p>
    <p>请先核对时间线、当前文件和可能的外部副作用，再在合适的会话中发起新任务，并手动提供已完成工作、未完成事项和不确定操作的交接信息。新任务不会自动恢复原运行。</p>
    {run.status === "budget_paused" && <p>新任务的预算需重新明确；请先查看原运行及子任务的用量记录，未知用量不等于零。</p>}
  </div>;
  const sameProject = (selectedProject ?? "inbox") === (project ?? "inbox");
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentSessionId === session &&
    (useSessionStore.getState().currentProjectId ?? "inbox") === (project ?? "inbox");
  const recoveryStep = [...(review?.snapshot.steps ?? [])].reverse().find((step) => typeof step.payload?.recovered_at === "string");
  const budgetPaused = run.status === "budget_paused" || (run.status === "interrupted" && recoveryStep?.payload?.previous_status === "budget_paused");
  const validBudget = /^[1-9]\d*$/.test(budgetTokens) && Number.isSafeInteger(Number(budgetTokens));
  const loadReview = async () => {
    if (!resumable || !session || session !== run.session_id || !sameProject || loading || pending) return;
    const ticket = ++epoch.current; setLoading(true); setReview(null); setAttempt(null); setReviewed(false); setError(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      const snapshot = await typedIPC.getRunSteps(run.id);
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current(ticket)) return;
      if (!identity.service_id || finalIdentity.service_id !== identity.service_id || snapshot.run.id !== run.id ||
          snapshot.run.session_id !== session || snapshot.run.status !== run.status ||
          (snapshot.run.metadata?.project_id ?? null) !== project || !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
          snapshot.steps.some((step) => step.run_id !== run.id || step.session_id !== session)) throw new Error("运行、项目或服务绑定已变化，请刷新");
      const scope = { host, service: identity.service_id, session, run: run.id };
      let saved: Attempt | null = null;
      try {
        const raw = localStorage.getItem(retryStorage(scope));
        if (raw) {
          const value = JSON.parse(raw) as Attempt;
          if (value.run !== run.id || value.session !== session || value.project !== project || value.host !== host || value.service !== identity.service_id ||
              typeof value.key !== "string" || !value.key || value.reviewed !== true || !Number.isSafeInteger(value.cursor) || value.cursor < 0 ||
              (value.budget !== null && (!Number.isSafeInteger(value.budget) || value.budget <= 0))) throw new Error("待确认记录无效，请手动检查");
          saved = value;
        }
      } catch (failure) { throw new Error(`无法读取安全重试记录：${failure instanceof Error ? failure.message : String(failure)}`); }
      setReview({ snapshot, service: identity.service_id, host, project }); setAttempt(saved);
      if (saved?.budget != null) { setBudgetTokens(String(saved.budget)); setChangeBudget(true); }
    } catch (failure) { if (current(ticket)) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (current(ticket)) setLoading(false); }
  };
  const act = async () => {
    if (inFlight.current) return;
    if (!resumable) {
      inFlight.current = true; setPending(true); setError(null);
      try {
        const reply = await typedIPC.cancelRun(run.id);
        if (!reply.requested && !["completed", "failed", "cancelled", "interrupted"].includes(reply.run.status)) throw new Error("任务未在当前服务中执行，请刷新并检查状态。");
        setCancelRequested(reply.requested); await refresh();
      } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
      finally { inFlight.current = false; setPending(false); }
      return;
    }
    if (!review || !reviewed || !session || session !== run.session_id || !sameProject || (budgetPaused && changeBudget && !attempt && !validBudget)) return;
    if (attempt && attempt.cursor !== review.snapshot.cursor) { setError("原运行记录已变化；请检查是否已有接续运行，不能直接重试旧请求。"); return; }
    const payload: Attempt = attempt ?? { key: crypto.randomUUID(), run: run.id, session, project, service: review.service,
      host, cursor: review.snapshot.cursor, budget: budgetPaused && changeBudget ? Number(budgetTokens) : null, reviewed: true };
    try { localStorage.setItem(retryStorage(payload), JSON.stringify(payload)); }
    catch { setError("浏览器存储不可用，无法保留重试身份；未发送恢复请求。"); return; }
    const ticket = ++epoch.current; setAttempt(payload); inFlight.current = true; setPending(true); setError(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current(ticket) || identity.service_id !== payload.service) throw new Error("服务或会话已变化，请重新核对");
      const result = await typedIPC.resumeRun(run.id, payload.key, true, payload.budget ?? undefined,
        { service: payload.service, session: payload.session, project: payload.project, cursor: payload.cursor });
      if (!current(ticket)) return;
      if (result.run.session_id !== session || result.run.resumed_from_id !== run.id) throw new Error("接续运行归属不匹配，请在时间线核对");
      localStorage.removeItem(retryStorage(payload)); setAttempt(null); setResumed(true); await refresh();
    } catch (failure) { if (current(ticket)) setError(`恢复结果未确认：${failure instanceof Error ? failure.message : String(failure)}。请先核对时间线；重试将使用同一请求。`); }
    finally { inFlight.current = false; if (current(ticket)) setPending(false); }
  };
  const forget = () => {
    if (!attempt || pending) return;
    if (!window.confirm("请先核对时间线及文件，确认是否已有接续运行。移除本地待确认记录不会取消或撤销已接收的任务。确定移除吗？")) return;
    try { localStorage.removeItem(retryStorage(attempt)); setAttempt(null); setReviewed(false); setError("本地记录已移除；实际运行和文件未改变。"); }
    catch { setError("无法移除本地记录，请检查浏览器存储。"); }
  };
  return <div className="mt-2 space-y-2 text-xs text-ink-1" data-testid="run-recovery-actions">
    {resumable ? <>
      <p>恢复会创建关联的新运行，不会自动重放工具步骤，也不是从精确执行位置继续。请先核对记录、当前文件和可能的外部副作用。</p>
      <Button type="button" size="sm" variant="secondary" disabled={loading || pending || !sameProject || session !== run.session_id} loading={loading} onClick={() => void loadReview()}>读取中断记录并核对</Button>
      {!sameProject && <p>请切换到运行所属项目后核对。</p>}
      {review && <div className="space-y-2 rounded border border-line p-2">
        <p className="break-all">运行 {run.id} · 目录 {String(run.metadata?.workspace_snapshot)} · 服务 {review.service}</p>
        <p>运行状态：{review.snapshot.run.status}；读取时记录版本：{review.snapshot.cursor}。仅展示已持久记录，缺失记录不代表没有副作用。</p>
        {recoveryStep && <p>重启前状态：{String(recoveryStep.payload?.previous_status ?? "未知")}；重启核对标记：{recoveryStep.payload?.requires_review === true ? "需要核对" : recoveryStep.payload?.requires_review === false ? "记录为不需要" : "未知"}。</p>}
        <p>已记录步骤 {review.snapshot.steps.length} 项；以下显示最近最多 50 项。结果未知的工具调用可能已经产生副作用，须检查当前文件或外部系统。</p>
        <ol className="max-h-56 space-y-1 overflow-auto">{review.snapshot.steps.slice(-50).map((step) => <li key={step.id} className="break-words rounded bg-surface-1 p-1">
          {step.kind} · {step.tool_name || step.title || "未命名"} · {step.status}{step.payload?.outcome === "unknown" || step.payload?.requires_review === true ? " · 结果未知，需核对" : ""}
          {step.error && <p>{step.error.slice(0, 500)}{step.error.length > 500 ? "…（截断）" : ""}</p>}
        </li>)}</ol>
        {review.snapshot.steps.length > 50 && <p>较早步骤未在此显示，请查看完整时间线。</p>}
        <RunWorkspaceDiff run={review.snapshot.run} />
        <label className="flex items-start gap-2"><input type="checkbox" checked={reviewed} disabled={pending} onChange={(event) => setReviewed(event.target.checked)} />已核对记录、当前文件及可能的外部副作用。</label>
        {budgetPaused && <div className="space-y-1">
          <label className="flex items-center gap-2"><input type="checkbox" checked={changeBudget} disabled={pending || !!attempt} onChange={(event) => setChangeBudget(event.target.checked)} />修改累计 token 上限</label>
          <p>默认保留原运行的累计上限（包括未设置上限），不清除已用额度或未知责任。日额度及账本检查仍然生效；如家族额度已变化，请先核对最新运行。</p>
          {changeBudget && <label className="block">恢复 token 上限（累计含历史与子任务）<input aria-label="恢复 token 上限" type="number" min="1" value={budgetTokens} disabled={pending || !!attempt} onChange={(event) => setBudgetTokens(event.target.value)} /></label>}
        </div>}
        {attempt && <p>存在待确认恢复请求；重试保留原请求 ID 和预算，不会创建新的重试身份。请先核对时间线。</p>}
        {attempt && attempt.cursor !== review.snapshot.cursor && <p role="status">原记录版本已变化，不能直接重试；请先核对是否已创建接续运行。</p>}
        <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => void act()} disabled={pending || resumed || !reviewed || (budgetPaused && changeBudget && !attempt && !validBudget) || (!!attempt && attempt.cursor !== review.snapshot.cursor)}>
          {pending ? "处理中…" : resumed ? "已创建接续运行" : attempt ? "重试同一恢复请求" : "确认恢复为新运行"}</Button>
          {attempt && <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={forget}>移除本地待确认记录</Button>}</div>
      </div>}
    </> : <Button size="sm" variant="secondary" onClick={() => void act()} disabled={pending || cancelRequested}>{pending ? "处理中…" : cancelRequested ? "正在取消…" : "取消此任务"}</Button>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
  </div>;
}
