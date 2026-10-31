import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { AgentRun, RunStepsResult } from "../../types/ipc";
import { Button } from "../../ui";
import { RunWorkspaceDiff } from "./RunWorkspaceDiff";
import { CodexRecoveryInput } from "./CodexRecoveryInput";

export type RetainedRecoveryDraft = { handoff: string; changeBudget: boolean; budgetTokens: string };
const retainedRecoveryDrafts = new Map<string, RetainedRecoveryDraft>();
let recoveryUnloadGuard = false;
const warnRecoveryUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
export const recoveryDraftKey = (host: string, service: string, project: string, session: string, run: string) => JSON.stringify([host, service, project, session, run]);
export const readRetainedRecovery = (key: string): RetainedRecoveryDraft | undefined => retainedRecoveryDrafts.get(key);
export const discardRetainedRecovery = (key: string): void => retainRecovery(key, null);
function retainRecovery(key: string | null, draft: RetainedRecoveryDraft | null) {
  if (!key) return;
  if (draft && (draft.handoff.trim() || draft.changeBudget || draft.budgetTokens)) retainedRecoveryDrafts.set(key, draft);
  else retainedRecoveryDrafts.delete(key);
  if (retainedRecoveryDrafts.size && !recoveryUnloadGuard) { window.addEventListener("beforeunload", warnRecoveryUnload); recoveryUnloadGuard = true; }
  if (!retainedRecoveryDrafts.size && recoveryUnloadGuard) { window.removeEventListener("beforeunload", warnRecoveryUnload); recoveryUnloadGuard = false; }
}
type Review = { snapshot: RunStepsResult; service: string; host: string; project: string | null };
type Attempt = { key: string; run: string; session: string; project: string | null; service: string; host: string; cursor: number; budget: number | null; handoff: string; reviewed: true };
const retryStorage = (item: Pick<Attempt, "host" | "service" | "session" | "run">) =>
  `minimax:resume:${encodeURIComponent(item.host)}:${encodeURIComponent(item.service)}:${encodeURIComponent(item.session)}:${encodeURIComponent(item.run)}`;

export function RunRecoveryActions({ run, refresh, expectedService, onEditingChange }: { run: AgentRun; refresh: () => Promise<void>; expectedService?: string; onEditingChange?: (editing: boolean) => void }): JSX.Element | null {
  const [reviewed, setReviewed] = useState(false), [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null), [cancelRequested, setCancelRequested] = useState(false);
  const [resumed, setResumed] = useState(false), [budgetTokens, setBudgetTokens] = useState("");
  const [changeBudget, setChangeBudget] = useState(false), [handoff, setHandoff] = useState("");
  const [review, setReview] = useState<Review | null>(null), [attempt, setAttempt] = useState<Attempt | null>(null);
  const [foundRun, setFoundRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(false);
  const epoch = useRef(0), inFlight = useRef(false);
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.sessions.find((item) => item.id === session)?.project_id ?? null);
  const selectedProject = useSessionStore((state) => state.currentProjectId);
  const host = ipc.agentBaseUrl;
  const draftKey = expectedService && session && project ? recoveryDraftKey(host, expectedService, project, session, run.id) : null;
  const saveEditing = (patch: Partial<RetainedRecoveryDraft>) => retainRecovery(draftKey, { handoff, changeBudget, budgetTokens, ...patch });
  const codex = run.metadata?.engine === "codex";
  const continuation = !codex && run.status === "completed" && run.metadata?.truncated === true;
  const scheduledContinuation = continuation && !!run.metadata?.schedule;
  const resumable = continuation || ["interrupted", "failed", "cancelled", "budget_paused"].includes(run.status);
  const active = ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status);
  useEffect(() => {
    const saved = draftKey ? retainedRecoveryDrafts.get(draftKey) : undefined;
    ++epoch.current; setReview(null); setAttempt(null); setFoundRun(null); setReviewed(false); setError(null); setLoading(false); setPending(false); setResumed(false); setCancelRequested(false); setBudgetTokens(saved?.budgetTokens ?? ""); setChangeBudget(saved?.changeBudget ?? false); setHandoff(saved?.handoff ?? "");
    return () => { ++epoch.current; };
  }, [run.id, run.status, session, project, selectedProject, host, draftKey]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setReview(null); setAttempt(null); setReviewed(false); setPending(false); setLoading(false); setError("连接变化，请重新读取运行和待确认请求。"); }), []);
  useEffect(() => { onEditingChange?.(!!handoff.trim() || changeBudget || !!budgetTokens || reviewed); }, [handoff, changeBudget, budgetTokens, reviewed, onEditingChange]);
  useEffect(() => () => onEditingChange?.(false), [onEditingChange]);
  if (!resumable && !active) return null;
  const hasWorkspace = typeof run.metadata?.workspace_snapshot === "string" && !!run.metadata.workspace_snapshot;
  const missingContext = [
    ...(!hasWorkspace ? ["工作区快照"] : []),
    ...(codex ? (!run.metadata?.codex_thread_id ? ["Codex 线程"] : []) : (!run.metadata?.model_snapshot ? ["模型快照"] : [])),
  ];
  if (!resumable && missingContext.length) return null;
  if (resumable && (run.mode === "team" || run.metadata?.manual_team_validation || run.metadata?.manual_project_validation)) return <p className="mt-2 text-xs text-status-warning">
    此运行不能直接接续。请先核对成员结果、工作区和可能的外部副作用，再明确发起新任务或验证。
  </p>;
  if (resumable && missingContext.length) return <div className="mt-2 space-y-1 text-xs text-status-warning" data-testid="run-recovery-unavailable">
    <p>无法直接接续：记录缺少{missingContext.join("、")}。</p>
    <p>先核对时间线、文件和外部副作用。新任务不会自动恢复，请手动交接进度和不确定事项。</p>
    {run.status === "budget_paused" && <p>新任务须重新明确预算；先核对原任务及子任务用量，未知不等于零。</p>}
  </div>;
  const sameProject = (selectedProject ?? "inbox") === (project ?? "inbox");
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentSessionId === session &&
    (useSessionStore.getState().currentProjectId ?? "inbox") === (project ?? "inbox");
  const recoveryStep = [...(review?.snapshot.steps ?? [])].reverse().find((step) => typeof step.payload?.recovered_at === "string");
  const budgetPaused = run.status === "budget_paused" || (run.status === "interrupted" && recoveryStep?.payload?.previous_status === "budget_paused");
  const canChangeBudget = !run.metadata?.schedule || (budgetPaused && !scheduledContinuation);
  const validBudget = /^[1-9]\d*$/.test(budgetTokens) && Number.isSafeInteger(Number(budgetTokens));
  const loadReview = async () => {
    if (!resumable || !session || session !== run.session_id || !sameProject || loading || pending) return;
    const ticket = ++epoch.current; setLoading(true); setReview(null); setAttempt(null); setReviewed(false); setError(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      const snapshot = await typedIPC.getRunSteps(run.id);
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current(ticket)) return;
      if (!identity.service_id || (expectedService && identity.service_id !== expectedService) || finalIdentity.service_id !== identity.service_id || snapshot.run.id !== run.id ||
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
              (value.budget !== null && (!Number.isSafeInteger(value.budget) || value.budget <= 0)) ||
              (value.handoff !== undefined && (typeof value.handoff !== "string" || value.handoff.length > 4000))) throw new Error("待确认记录无效，请手动检查");
          saved = { ...value, handoff: value.handoff ?? "" };
        }
      } catch (failure) { throw new Error(`无法读取安全重试记录：${failure instanceof Error ? failure.message : String(failure)}`); }
      setReview({ snapshot, service: identity.service_id, host, project }); setAttempt(saved);
      if (saved) setHandoff(saved.handoff);
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
    if (!review || !reviewed || !session || session !== run.session_id || !sameProject || (canChangeBudget && changeBudget && !attempt && !validBudget)) return;
    if (attempt && attempt.cursor !== review.snapshot.cursor) { setError("原运行记录已变化；请检查是否已有接续运行，不能直接重试旧请求。"); return; }
    const payload: Attempt = attempt ?? { key: crypto.randomUUID(), run: run.id, session, project, service: review.service,
      host, cursor: review.snapshot.cursor, budget: canChangeBudget && changeBudget ? Number(budgetTokens) : null, handoff, reviewed: true };
    try { localStorage.setItem(retryStorage(payload), JSON.stringify(payload)); }
    catch { setError("浏览器存储不可用，无法保留重试身份；未发送恢复请求。"); return; }
    const ticket = ++epoch.current; setAttempt(payload); inFlight.current = true; setPending(true); setError(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current(ticket) || identity.service_id !== payload.service) throw new Error("服务或会话已变化，请重新核对");
      const result = await typedIPC.resumeRun(run.id, payload.key, true, payload.budget ?? undefined,
        { service: payload.service, session: payload.session, project: payload.project, cursor: payload.cursor }, payload.handoff);
      if (!current(ticket)) return;
      if (result.run.session_id !== session || result.run.resumed_from_id !== run.id) throw new Error("接续运行归属不匹配，请在时间线核对");
      localStorage.removeItem(retryStorage(payload)); retainRecovery(draftKey, null); setAttempt(null); setResumed(true); await refresh();
    } catch (failure) { if (current(ticket)) setError(`恢复结果未确认：${failure instanceof Error ? failure.message : String(failure)}。请先核对时间线；重试将使用同一请求。`); }
    finally { inFlight.current = false; if (current(ticket)) setPending(false); }
  };
  const forget = () => {
    if (!attempt || pending) return;
    if (!window.confirm("请先核对时间线及文件，确认是否已有接续运行。移除本地待确认记录不会取消或撤销已接收的任务。确定移除吗？")) return;
    try { localStorage.removeItem(retryStorage(attempt)); setAttempt(null); setReviewed(false); setError("本地记录已移除；实际运行和文件未改变。"); }
    catch { setError("无法移除本地记录，请检查浏览器存储。"); }
  };
  const findAccepted = async () => {
    if (!attempt || inFlight.current || !session || session !== run.session_id || !sameProject) return;
    const ticket = ++epoch.current;
    inFlight.current = true; setPending(true); setError(null); setFoundRun(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current(ticket) || identity.service_id !== attempt.service) throw new Error("服务或会话已变化，请重新核对");
      const candidates = (await typedIPC.listRuns({ recovery_lookup: {
        run_id: run.id, idempotency_key: attempt.key, session_id: session,
        project_id: project, service_id: attempt.service,
      } })).runs;
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current(ticket) || finalIdentity.service_id !== attempt.service) {
        throw new Error("服务、会话或项目已变化，请重新核对");
      }
      const matches = candidates.filter((item) => item.session_id === session && item.resumed_from_id === run.id &&
        item.request_key === attempt.key && (item.metadata?.project_id ?? null) === project);
      if (matches.length !== 1) throw new Error("未能确认此请求是否已接收；请检查完整时间线，保留待确认记录");
      setFoundRun(matches[0]);
      localStorage.removeItem(retryStorage(attempt)); retainRecovery(draftKey, null); setAttempt(null); setResumed(true);
      await refresh();
    } catch (failure) { if (current(ticket)) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { inFlight.current = false; if (current(ticket)) setPending(false); }
  };
  return <div className="mt-2 min-w-0 space-y-2 break-words text-xs text-ink-1" data-testid="run-recovery-actions">
    {resumable ? <>
      <p>恢复会创建新运行，不会重放工具或精确续接。请先核对记录、文件和外部副作用。</p>
      {continuation && !scheduledContinuation && <p>此运行达到迭代上限后尚未完成；核对后可在累计预算下继续剩余工作。</p>}
      {scheduledContinuation && <p>定时运行达到迭代上限。核对后按原记录和预算继续；后续触发仍须按任务状态处理。</p>}
      <Button type="button" size="sm" variant="secondary" disabled={loading || pending || !sameProject || session !== run.session_id} loading={loading} onClick={() => void loadReview()}>读取中断记录并核对</Button>
      {!sameProject && <p>请切换到运行所属项目后核对。</p>}
      {review && <div className="space-y-2 rounded border border-line p-2">
        <p className="break-all">运行 {run.id} · 目录 {String(run.metadata?.workspace_snapshot)} · 服务 {review.service}</p>
        <p>运行状态：{review.snapshot.run.status}；读取时记录版本：{review.snapshot.cursor}。仅展示已持久记录，缺失记录不代表没有副作用。</p>
        {recoveryStep && <p>重启前状态：{String(recoveryStep.payload?.previous_status ?? "未知")}；重启核对标记：{recoveryStep.payload?.requires_review === true ? "需要核对" : recoveryStep.payload?.requires_review === false ? "记录为不需要" : "未知"}。</p>}
        <p>已记录 {review.snapshot.steps.length} 步，显示最近 50 步。未知调用可能已有副作用，请检查文件或外部系统。</p>
        <ol className="max-h-56 space-y-1 overflow-auto">{review.snapshot.steps.slice(-50).map((step) => <li key={step.id} className="break-words rounded bg-surface-1 p-1">
          {step.kind} · {step.tool_name || step.title || "未命名"} · {step.status}{step.payload?.outcome === "unknown" || step.payload?.requires_review === true ? " · 结果未知，需核对" : ""}
          {step.error && <p>{step.error.slice(0, 500)}{step.error.length > 500 ? "…（截断）" : ""}</p>}
        </li>)}</ol>
        {review.snapshot.steps.length > 50 && <p>较早步骤未在此显示，请查看完整时间线。</p>}
        {review.snapshot.steps.some((step) => step.payload?.source === "native_steer" && step.status !== "completed" && typeof step.payload.text === "string") && <div className="space-y-1 rounded border border-line p-2">
          <p>以下补充未确认生效，请先核对记录避免重复。可加入交接草稿并在恢复前编辑。</p>
          {review.snapshot.steps.filter((step) => step.payload?.source === "native_steer" && step.status !== "completed" && typeof step.payload.text === "string").map((step) => {
            const text = String(step.payload?.text ?? "");
            return <div key={step.id} className="space-y-1 rounded bg-surface-1 p-2">
              <p>状态：{step.status} · {step.summary || "未确认"}</p>
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words">{text}</pre>
              <Button type="button" size="sm" variant="secondary" disabled={pending || !!attempt} onClick={() => {
                const addition = `${handoff.trim() ? "\n\n" : ""}待核对的运行中补充（是否生效未知）：\n${text}`;
                if (handoff.length + addition.length > 4000) { setError("交接超过 4000 字，请手动摘录需要的内容。"); return; }
                setHandoff(handoff + addition); saveEditing({ handoff: handoff + addition }); setError(null);
              }}>加入恢复交接草稿</Button>
            </div>;
          })}
        </div>}
        <RunWorkspaceDiff run={review.snapshot.run} />
        {session && <CodexRecoveryInput run={run.id} session={session} project={project} service={review.service} engine={codex ? "codex" : "native"}
          cursor={review.snapshot.cursor} disabled={pending || !!attempt} onAdd={(text) => {
            const addition = `${handoff.trim() ? "\n\n" : ""}原任务文字摘录（请核对是否已被处理）：\n${text}`;
            if (handoff.length + addition.length > 4000) return "交接超过 4000 字，请缩短摘录或已有交接。";
            setHandoff(handoff + addition); saveEditing({ handoff: handoff + addition }); setReviewed(false);
            return "已加入恢复交接；请再次审阅并确认。";
          }} />}
        <label className="flex items-start gap-2"><input type="checkbox" checked={reviewed} disabled={pending} onChange={(event) => setReviewed(event.target.checked)} />已核对记录、当前文件及可能的外部副作用。</label>
        <label className="block">恢复交接（可选）：记录已核实的操作、仍不确定的结果和剩余事项
          <textarea aria-label="恢复交接" className="mt-1 block w-full rounded border border-line bg-surface-1 p-2" maxLength={4000} value={handoff} disabled={pending || !!attempt} onChange={(event) => { setHandoff(event.target.value); saveEditing({ handoff: event.target.value }); }} />
        </label>
        <p>交接内容会随恢复请求保存并发送给模型；不要填写不应发送的秘密。</p>
        {draftKey && <div><p>未提交编辑仅保留在页面内存，刷新或关闭即丢失；重新打开须再次核对确认。</p>
          <Button type="button" size="sm" variant="ghost" disabled={pending || !!attempt} onClick={() => { if (window.confirm("放弃此运行未提交的恢复交接和预算编辑？")) { retainRecovery(draftKey, null); setHandoff(""); setChangeBudget(false); setBudgetTokens(""); setReviewed(false); } }}>放弃恢复编辑</Button></div>}
        {canChangeBudget && <div className="space-y-1">
          <label className="flex items-center gap-2"><input type="checkbox" checked={changeBudget} disabled={pending || !!attempt} onChange={(event) => { setChangeBudget(event.target.checked); saveEditing({ changeBudget: event.target.checked }); }} />修改累计 token 上限</label>
          <p>默认保留原累计上限，可提高但不清除用量或未知责任。日额度检查仍生效，请先核对账本。</p>
          {changeBudget && <label className="block">恢复 token 上限（累计含历史与子任务）<input aria-label="恢复 token 上限" type="number" min="1" className="block w-full min-w-0" value={budgetTokens} disabled={pending || !!attempt} onChange={(event) => { setBudgetTokens(event.target.value); saveEditing({ budgetTokens: event.target.value }); }} /></label>}
        </div>}
        {attempt && <p>存在待确认恢复请求；重试保留原请求 ID 和预算，不会创建新的重试身份。请先核对时间线。</p>}
        {foundRun && <p role="status" className="break-all">已找到该请求接收的关联运行 {foundRun.id}，当前记录状态：{foundRun.status}。请在时间线查看其结果。</p>}
        {attempt && attempt.cursor !== review.snapshot.cursor && <p role="status">原记录版本已变化，不能直接重试；请先核对是否已创建接续运行。</p>}
        <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => void act()} disabled={pending || resumed || !reviewed || (canChangeBudget && changeBudget && !attempt && !validBudget) || (!!attempt && attempt.cursor !== review.snapshot.cursor)}>
          {pending ? "处理中…" : resumed ? "已创建接续运行" : attempt ? "重试同一恢复请求" : "确认恢复为新运行"}</Button>
          {attempt && <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => void findAccepted()}>查找已接收的接续运行</Button>}
          {attempt && <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={forget}>移除本地待确认记录</Button>}</div>
      </div>}
    </> : <Button size="sm" variant="secondary" onClick={() => void act()} disabled={pending || cancelRequested}>{pending ? "处理中…" : cancelRequested ? "正在取消…" : "取消此任务"}</Button>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
  </div>;
}
