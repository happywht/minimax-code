import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, usePermissionStore, useRunTimelineStore, useSessionStore } from "../../stores";
import type { BudgetStateResult } from "../../types/ipc";
import { Button } from "../../ui";
import { QueuedRuns } from "./QueuedRuns";
import { InterruptedPlanDraft } from "./InterruptedPlanDraft";

const labels: Record<string, string> = { queued: "已接收，排队中", planning: "规划中", running: "运行中",
  awaiting_approval: "等待授权", waiting_input: "等待回答", budget_paused: "预算暂停", interrupted: "已中断，待核对",
  completed: "执行结束", failed: "执行失败", cancelled: "已取消" };
const active = new Set(["planning", "running", "awaiting_approval", "waiting_input"]);

type Connection = "connecting" | "connected" | "disconnected" | "error";
export function CurrentRunOverview({ connection, onOpenTimeline }: { connection: Connection; onOpenTimeline: () => void }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.currentProjectId);
  const sessionProject = useSessionStore((state) => state.sessions.find((item) => item.id === session)?.project_id);
  const runs = useRunTimelineStore((state) => state.runs), order = useRunTimelineStore((state) => state.order);
  const loading = useRunTimelineStore((state) => state.loading), error = useRunTimelineStore((state) => state.error);
  const loadRuns = useRunTimelineStore((state) => state.loadForSession);
  const permissions = usePermissionStore((state) => state.pending);
  const resolving = usePermissionStore((state) => state.resolving);
  const question = useChat((state) => state.askUser);
  const ordered = order.map((id) => runs[id]).filter((run) => run && run.session_id === session && !run.metadata?.manual_research);
  const run = ordered.find((item) => active.has(item.status)) ?? ordered.find((item) => item.status === "queued") ?? ordered[0];
  const [budget, setBudget] = useState<{ scope: string; value: BudgetStateResult | null; error?: string } | null>(null);
  const [planDraftMessage, setPlanDraftMessage] = useState("");
  const [deliveryBusy, setDeliveryBusy] = useState(false), [deliveryError, setDeliveryError] = useState("");
  const deliveryEpoch = useRef(0), connectionRef = useRef(connection);
  connectionRef.current = connection;
  const generation = useRef(0), runRef = useRef<string | null>(null);
  runRef.current = run?.id ?? null;
  const host = ipc.agentBaseUrl, scope = JSON.stringify([host, session, run?.id]);
  useEffect(() => { void loadRuns(session); }, [session, project, loadRuns]);
  useEffect(() => {
    deliveryEpoch.current++; setDeliveryBusy(false); setDeliveryError("");
    return () => { deliveryEpoch.current++; };
  }, [host, project, session, sessionProject, run?.id, connection]);
  useEffect(() => ipc.onSideCar(() => {
    deliveryEpoch.current++; setDeliveryBusy(false); setDeliveryError("连接已变化，请重试。");
  }), []);
  const refreshBudget = useCallback(async () => {
    if (!run || !session || connection !== "connected") return;
    const ticket = ++generation.current, id = run.id;
    const current = () => ticket === generation.current && runRef.current === id && ipc.agentBaseUrl === host && useSessionStore.getState().currentSessionId === session;
    setBudget(null);
    try { const value = await typedIPC.getBudget(id); if (current()) setBudget({ scope, value }); }
    catch (e) { if (current()) setBudget({ scope, value: null, error: e instanceof Error ? e.message : String(e) }); }
  }, [connection, host, run?.id, scope, session]);
  useEffect(() => { generation.current++; setBudget(null); if (connection === "connected") void refreshBudget();
    return () => { generation.current++; }; }, [refreshBudget, run?.status]);
  if (!session) return null;
  const stale = connection !== "connected" || !!error || loading;
  const record = budget?.scope === scope ? budget.value : null;
  const data = record?.budget;
  const md = run?.metadata;
  const snapshot = md?.model_snapshot;
  const codex = md?.engine === "codex";
  const settings = codex && md?.codex_settings && typeof md.codex_settings === "object" ? md.codex_settings as Record<string, unknown> : null;
  const engine = codex ? "Codex" : snapshot?.engine === "native" ? "Native" : "未知";
  const model = codex ? (typeof settings?.model === "string" && settings.model ? settings.model : "未记录") : snapshot?.model || "未记录";
  const workspace = typeof md?.workspace_snapshot === "string" && md.workspace_snapshot ? md.workspace_snapshot : "未记录";
  const permission = run && Object.values(permissions).find((p) => p.session_id === session && p.run_id === run.id);
  const awaitingQuestion = !!run && question?.session_id === session && question?.run_id === run.id;
  const requestId = !stale && run?.status === "awaiting_approval" && permission && !resolving[permission.request_id] ? permission.request_id :
    !stale && run?.status === "waiting_input" && awaitingQuestion ? question?.request_id : undefined;
  const openRequest = () => {
    if (!run || !requestId || connection !== "connected" || ipc.agentBaseUrl !== host || useSessionStore.getState().currentSessionId !== session) return;
    const state = useRunTimelineStore.getState();
    const currentRun = state.runs[run.id];
    if (state.loading || state.error || currentRun?.session_id !== session || currentRun.status !== run.status) return;
    if (run.status === "awaiting_approval") {
      const current = usePermissionStore.getState().pending[requestId];
      if (!current || current.session_id !== session || current.run_id !== run.id || usePermissionStore.getState().resolving[requestId]) return;
      window.dispatchEvent(new CustomEvent("minimax:open-run-permission", { detail: { host, session, run: run.id, request: requestId } }));
    } else if (run.status === "waiting_input") {
      const current = useChat.getState().askUser;
      if (!current || current.request_id !== requestId || current.session_id !== session || current.run_id !== run.id) return;
      window.dispatchEvent(new CustomEvent("minimax:open-run-question", { detail: { host, session, run: run.id, request: requestId } }));
    }
  };
  const openDeliveries = async () => {
    if (!run || !session || !project || stale || connection !== "connected") return;
    const ticket = ++deliveryEpoch.current, runId = run.id;
    const current = () => {
      const sessions = useSessionStore.getState(), timeline = useRunTimelineStore.getState();
      const selected = timeline.runs[runId];
      return ticket === deliveryEpoch.current && connectionRef.current === "connected" && ipc.agentBaseUrl === host && runRef.current === runId &&
        sessions.currentSessionId === session && sessions.currentProjectId === project &&
        sessions.sessions.find((item) => item.id === session)?.project_id === project &&
        !timeline.loading && !timeline.error && selected?.session_id === session && selected.metadata?.project_id === project;
    };
    setDeliveryBusy(true); setDeliveryError("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (!identity.service_id) throw new Error("无法确认服务身份");
      window.dispatchEvent(new CustomEvent("minimax:open-run-deliveries", {
        detail: { host, service: identity.service_id, project, session, run: runId },
      }));
    } catch (reason) { if (current()) setDeliveryError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === deliveryEpoch.current) setDeliveryBusy(false); }
  };
  const prepareStructuredPlan = () => {
    const sessions = useSessionStore.getState();
    const timeline = useRunTimelineStore.getState();
    const current = run && timeline.runs[run.id];
    const plan = current?.metadata?.codex_plan;
    if (!run || !session || ipc.agentBaseUrl !== host || sessions.currentSessionId !== session ||
        timeline.loading || timeline.error || current?.session_id !== session || current.mode !== "plan" ||
        current.status !== "completed" || !plan || plan.truncated || (!plan.explanation && !plan.steps.length) ||
        ["sending", "streaming", "cancelling"].includes(useChat.getState().status)) {
      setPlanDraftMessage("计划或会话状态已变化，请刷新后重试。");
      return;
    }
    const detail: { host: string; project: string | null; session: string; text: string; result?: string } = {
      host, project: sessions.currentProjectId, session,
      text: `请执行以下我选择的计划；执行前核对当前工作区和必要授权。遇到需要我决定的事项请先询问。\n\n所选规划运行：${current.id}\n\n${plan.explanation ?? ""}\n${plan.steps.map((step, index) => `${index + 1}. ${step.step} (${step.status})`).join("\n")}`,
    };
    window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
    if (detail.result === "已添加到当前会话草稿，尚未发送。") {
      useChat.getState().setExecutionMode("execute");
      setPlanDraftMessage("计划已加入草稿并切换到执行模式；请核对、设置预算后发送。");
    } else setPlanDraftMessage(detail.result ?? "聊天输入区未就绪，请稍后重试。");
  };
  return <div className="shrink-0 border-b border-line bg-surface-1 px-4 py-2 text-xs">
    {requestId && <div className="mb-2 flex flex-wrap items-center justify-between gap-2" role="status">
      <span className="font-medium text-status-warning">{run?.status === "awaiting_approval" ? "当前任务等待授权" : "当前任务等待回答"}</span>
      <Button type="button" size="sm" variant="secondary" onClick={openRequest}>{run?.status === "awaiting_approval" ? "查看授权请求" : "回答问题"}</Button>
    </div>}
    {!stale && run && ["budget_paused", "failed", "interrupted", "cancelled"].includes(run.status) && <div className="mb-2 flex flex-wrap items-center justify-between gap-2" role="status">
      <span className="text-status-warning">{labels[run.status]} · 请核对记录和可用操作</span>
      <Button type="button" size="sm" variant="secondary" onClick={onOpenTimeline}>查看任务记录与下一步</Button>
    </div>}
    <details data-testid="current-run-overview">
    <summary className="cursor-pointer font-medium text-ink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">任务概览 · {stale ? "状态待刷新" : run ? labels[run.status] ?? run.status : loading ? "正在读取" : "暂无运行记录"}</summary>
    {stale && <p role="status" className="mt-1 text-status-warning">连接或运行记录未确认；以下为最近快照，操作前请刷新。</p>}
    {error && <p role="alert" className="mt-1 break-words text-status-warning">运行记录同步失败：{error}</p>}
    <Button type="button" size="sm" variant="ghost" disabled={connection !== "connected" || loading} onClick={() => void loadRuns(session)}>刷新运行记录</Button>
    {!run ? <p className="mt-1 text-ink-2">{error ? "无法读取当前会话的运行记录。" : loading ? "正在读取运行记录。" : "此会话尚无可显示的任务运行。"}</p> : <div className="mt-2 space-y-1 text-ink-1">
      <p className="break-all">{active.has(run.status) ? "当前活动运行" : run.status === "queued" ? "已接收的排队任务" : "最近运行"}：{run.title || "未命名"} · {run.id}</p>
      <p>状态：{labels[run.status] ?? run.status}{run.status === "awaiting_approval" ? !stale && permission ? ` · 请求 ${permission.tool}` : " · 请求详情待同步或已结束" : ""}{run.status === "waiting_input" ? !stale && awaitingQuestion ? " · 问题卡片在聊天下方" : " · 问题详情待同步或已结束" : ""}</p>
      <p className="break-all">服务地址：{host} · 执行主机/容器：未记录</p>
      <p className="break-all">执行目录（服务侧记录）：{workspace}</p>
      <p>引擎：{engine} · 模型：{model}</p>
      {codex && <div className="space-y-1" data-testid="codex-usage">
        <p>Codex 引擎报告的本运行用量：{md?.codex_usage ? `输入 ${md.codex_usage.input_tokens} · 输出 ${md.codex_usage.output_tokens} · 合计 ${md.codex_usage.total_tokens}` : "尚无用量报告"}</p>
        <p>{md?.usage_certainty === "observed" ? "已收到引擎用量更新；运行结束前仍可能增加。" : md?.usage_certainty === "recovery_interval" ? "包含恢复期间的用量变化，不能精确归属到单次运行。" : md?.usage_certainty === "uncertain" ? "用量水位异常，当前数字不完整；请核对预算账本。" : "用量尚未确认，不能按零计算。"}</p>
        <p>这是引擎报告，不是费用或严格预算保证；共享预算请查看预算账本。</p>
      </div>}
      {codex && md?.codex_plan && <div className="space-y-1" data-testid="codex-plan">
        <p className="font-medium">Codex 计划（引擎报告）</p>
        {md.codex_plan.explanation && <p className="whitespace-pre-wrap break-words">{md.codex_plan.explanation}</p>}
        {md.codex_plan.steps.length > 0 ? <ol className="list-inside list-decimal space-y-1">
          {md.codex_plan.steps.map((step, index) => <li key={index} className="whitespace-pre-wrap break-words">
            {step.step} · {step.status === "completed" ? "已完成" : step.status === "inProgress" ? "进行中" : "待处理"}
          </li>)}
        </ol> : <p>当前没有计划步骤。</p>}
        {md.codex_plan.truncated && <p>计划内容过长，显示已截断。</p>}
        <InterruptedPlanDraft run={run} />
        {run.mode === "plan" && run.status === "completed" && !md.codex_plan.truncated &&
          (md.codex_plan.explanation || md.codex_plan.steps.length > 0) && <div className="space-y-1">
            <Button type="button" size="sm" variant="secondary" disabled={stale} onClick={prepareStructuredPlan}>准备执行此计划</Button>
            {planDraftMessage && <p role="status">{planDraftMessage}</p>}
          </div>}
      </div>}
      <p>预算快照：{stale ? "状态待刷新" : data ? `累计已知${data.usage_complete === true ? "" : "至少 "}${data.known_tokens} · 预留/责任 ${data.reserved_tokens} · 上限 ${data.limit_tokens ?? "未设置"} · 剩余估算 ${data.remaining_estimate ?? "未知"}` : budget?.scope === scope && budget.error ? "读取失败" : record ? "该运行没有预算记录" : "读取中或未知"}</p>
      {data && data.unknown_attempts > 0 && <p>存在 {data.unknown_attempts} 个结果不明的请求，需在预算账本核对。</p>}
      <div className="flex flex-wrap gap-2 pt-1"><Button type="button" size="sm" variant="secondary" onClick={onOpenTimeline}>查看时间线与可用操作</Button>
        {project && sessionProject === project && run.metadata?.project_id === project && <Button type="button" size="sm" variant="secondary" disabled={stale || deliveryBusy} onClick={() => void openDeliveries()}>{deliveryBusy ? "正在确认服务…" : "查看此任务的交付物"}</Button>}
        <Button type="button" size="sm" variant="ghost" disabled={connection !== "connected"} onClick={() => void refreshBudget()}>刷新预算</Button></div>
      {deliveryError && <p role="alert" className="text-status-warning">无法打开任务成果：{deliveryError}</p>}
      <p className="text-ink-2">预算为读取时快照；时间线保留审批、结果、取消及恢复的原有条件。</p>
    </div>}
    <QueuedRuns session={session} host={host} runs={ordered.filter((item) => item.status === "queued")} stale={stale} />
  </details></div>;
}
