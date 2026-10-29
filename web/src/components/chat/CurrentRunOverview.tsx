import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, usePermissionStore, useRunTimelineStore, useSessionStore } from "../../stores";
import type { BudgetStateResult } from "../../types/ipc";
import { Button } from "../../ui";

const labels: Record<string, string> = { queued: "已接收，排队中", planning: "规划中", running: "运行中",
  awaiting_approval: "等待授权", waiting_input: "等待回答", budget_paused: "预算暂停", interrupted: "已中断，待核对",
  completed: "执行结束", failed: "执行失败", cancelled: "已取消" };
const active = new Set(["planning", "running", "awaiting_approval", "waiting_input"]);

type Connection = "connecting" | "connected" | "disconnected" | "error";
export function CurrentRunOverview({ connection, onOpenTimeline }: { connection: Connection; onOpenTimeline: () => void }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const runs = useRunTimelineStore((state) => state.runs), order = useRunTimelineStore((state) => state.order);
  const loading = useRunTimelineStore((state) => state.loading), error = useRunTimelineStore((state) => state.error);
  const loadRuns = useRunTimelineStore((state) => state.loadForSession);
  const permissions = usePermissionStore((state) => state.pending);
  const resolving = usePermissionStore((state) => state.resolving);
  const question = useChat((state) => state.askUser);
  const ordered = order.map((id) => runs[id]).filter((run) => run && run.session_id === session && !run.metadata?.manual_research);
  const run = ordered.find((item) => active.has(item.status)) ?? ordered.find((item) => item.status === "queued") ?? ordered[0];
  const [budget, setBudget] = useState<{ scope: string; value: BudgetStateResult | null; error?: string } | null>(null);
  const generation = useRef(0), runRef = useRef<string | null>(null);
  runRef.current = run?.id ?? null;
  const host = ipc.agentBaseUrl, scope = JSON.stringify([host, session, run?.id]);
  useEffect(() => { void loadRuns(session); }, [session, loadRuns]);
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
  return <div className="shrink-0 border-b border-line bg-surface-1 px-4 py-2 text-xs">
    {requestId && <div className="mb-2 flex flex-wrap items-center justify-between gap-2" role="status">
      <span className="font-medium text-status-warning">{run?.status === "awaiting_approval" ? "当前任务等待授权" : "当前任务等待回答"}</span>
      <Button type="button" size="sm" variant="secondary" onClick={openRequest}>{run?.status === "awaiting_approval" ? "查看授权请求" : "回答问题"}</Button>
    </div>}
    <details data-testid="current-run-overview">
    <summary className="cursor-pointer font-medium text-ink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">任务概览 · {stale ? "状态待刷新" : run ? labels[run.status] ?? run.status : loading ? "正在读取" : "暂无运行记录"}</summary>
    {stale && <p role="status" className="mt-1 text-status-warning">连接或运行记录未确认；以下为最近快照，操作前请刷新。</p>}
    {!run ? <p className="mt-1 text-ink-2">{error ? "无法读取当前会话的运行记录。" : loading ? "正在读取运行记录。" : "此会话尚无可显示的任务运行。"}</p> : <div className="mt-2 space-y-1 text-ink-1">
      <p className="break-all">{active.has(run.status) ? "当前活动运行" : run.status === "queued" ? "已接收的排队任务" : "最近运行"}：{run.title || "未命名"} · {run.id}</p>
      <p>状态：{labels[run.status] ?? run.status}{run.status === "awaiting_approval" ? !stale && permission ? ` · 请求 ${permission.tool}` : " · 请求详情待同步或已结束" : ""}{run.status === "waiting_input" ? !stale && awaitingQuestion ? " · 问题卡片在聊天下方" : " · 问题详情待同步或已结束" : ""}</p>
      <p className="break-all">服务地址：{host} · 执行主机/容器：未记录</p>
      <p className="break-all">执行目录（服务侧记录）：{workspace}</p>
      <p>引擎：{engine} · 模型：{model}</p>
      <p>预算快照：{stale ? "状态待刷新" : data ? `累计已知${data.usage_complete === true ? "" : "至少 "}${data.known_tokens} · 预留/责任 ${data.reserved_tokens} · 上限 ${data.limit_tokens ?? "未设置"} · 剩余估算 ${data.remaining_estimate ?? "未知"}` : budget?.scope === scope && budget.error ? "读取失败" : record ? "该运行没有预算记录" : "读取中或未知"}</p>
      {data && data.unknown_attempts > 0 && <p>存在 {data.unknown_attempts} 个结果不明的请求，需在预算账本核对。</p>}
      <div className="flex flex-wrap gap-2 pt-1"><Button type="button" size="sm" variant="secondary" onClick={onOpenTimeline}>查看时间线与可用操作</Button>
        <Button type="button" size="sm" variant="ghost" disabled={connection !== "connected"} onClick={() => void refreshBudget()}>刷新预算</Button></div>
      <p className="text-ink-2">预算为读取时快照；时间线保留审批、结果、取消及恢复的原有条件。</p>
    </div>}
  </details></div>;
}
