import { useCallback, useEffect, useRef, useState } from "react";
import { typedIPC } from "../../ipc";
import type { AgentRun, BudgetReconcileParams, BudgetStateResult } from "../../types/ipc";

const reasons: Record<string, string> = {
  unresolved_attempt: "存在结果不明的模型请求，请核对其责任额度",
  legacy_family_incomplete: "旧任务累计用量不完整，需要明确核对",
  daily_accounting_incomplete: "今日历史或请求责任不完整，日额度已暂停",
  family_ceiling: "任务累计额度已达上限",
  daily_ceiling: "本机今日额度已达上限",
  budget_storage_unavailable: "预算存储或恢复失败；请重启并确认恢复成功后再发送付费请求",
};

const attemptStates: Record<string, string> = {
  reserved: "已预留，尚未发送",
  sent: "已发送，尚未结算",
  settled: "已结算",
  released: "已释放",
  unknown: "结果不明，需核对",
  reconciled: "已核定责任",
};

export function RunBudgetPanel({ run }: { run: AgentRun }): JSX.Element {
  const [loaded, setLoaded] = useState<{runId: string; state: BudgetStateResult} | null>(null);
  const runId = useRef(run.id);
  runId.current = run.id;
  const generation = useRef(0);
  const state = loaded?.runId === run.id ? loaded.state : null;
  const [error, setError] = useState<string | null>(null);
  const [liability, setLiability] = useState("");
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false);
  const load = useCallback(async () => {
    const request = ++generation.current;
    const current = () => runId.current === run.id && generation.current === request;
    try {
      const result = await typedIPC.getBudget(run.id);
      if (current()) { setLoaded({runId: run.id, state: result}); setError(null); }
    } catch (err) { if (current()) setError(err instanceof Error ? err.message : String(err)); }
  }, [run.id]);
  useEffect(() => {
    setLoaded(null); setError(null); setLiability(""); setReason("");
    setAcknowledged(false); setPending(false);
  }, [run.id]);
  useEffect(() => { void load(); }, [load, run.status, run.completed_at]);
  const budget = state?.budget;
  const targets: Array<{target: BudgetReconcileParams["target"]; id: string; label: string; minimum: number}> = [];
  if (state?.day.baseline_incomplete) targets.push({target: "day", id: state.day.day, label: "核定当日历史责任", minimum: state.day.historical_lower_bound});
  if (budget?.baseline_incomplete) targets.push({target: "family", id: budget.family_id, label: "核定旧任务历史责任", minimum: budget.known_tokens});
  for (const attempt of state?.attempts ?? []) {
    if (attempt.state === "unknown") targets.push({target: "attempt", id: attempt.id, label: `核定请求 ${attempt.id.slice(-8)} 责任`, minimum: attempt.known_tokens ?? 0});
  }
  const active = ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status);
  const reconcile = async (target: typeof targets[number]) => {
    const owner = run.id;
    if (!state || runId.current !== owner) return;
    setPending(true);
    try {
      await typedIPC.reconcileBudget({target: target.target, target_id: target.id,
        liability_tokens: Number(liability), reason, acknowledged: true});
      if (runId.current === owner) { await load(); setAcknowledged(false); }
    } catch (err) { if (runId.current === owner) setError(err instanceof Error ? err.message : String(err)); }
    finally { if (runId.current === owner) setPending(false); }
  };
  const reviewOwner = async (sessionId: string, ownerRunId: string) => {
    try {
      const {useSessionStore, useRunTimelineStore} = await import("../../stores");
      if (!useSessionStore.getState().setCurrent(sessionId)) return;
      await useRunTimelineStore.getState().loadForSession(sessionId);
      const snapshot = await typedIPC.getRunSteps(ownerRunId);
      if (useSessionStore.getState().currentSessionId !== sessionId) return;
      useRunTimelineStore.setState((previous) => ({
        runs: {...previous.runs, [ownerRunId]: {...snapshot.run, steps: snapshot.steps}},
        order: [ownerRunId, ...previous.order.filter((id) => id !== ownerRunId)],
      }));
    } catch (err) { if (runId.current === run.id) setError(err instanceof Error ? err.message : String(err)); }
  };
  return <div data-testid="run-budget" className="my-2 space-y-1 text-xs text-minimax-muted">
    <div className="flex justify-between"><strong>预算账本</strong><button type="button" onClick={() => void load()}>刷新额度</button></div>
    {budget ? <>
      <p>{budget.enforcement === "reactive" ? "Codex · 用量通知驱动中断" : "Native · 请求发送前估算预留"}</p>
      <p>任务累计已知{budget.usage_complete ? "" : "至少"} {budget.known_tokens} · 预留/核定责任 {budget.reserved_tokens} · 结果不明请求 {budget.unknown_attempts}</p>
      <p>剩余估算 {budget.remaining_estimate ?? "未设置上限"} · 累计上限 {budget.limit_tokens ?? "未设置"}</p>
      <p>此额度由同一预算家族的父子任务及恢复运行共享，以下合计并非当前运行独占。</p>
      <p>本机 {budget.day.day} · 已知{budget.day.usage_complete === true ? "" : "至少"} {budget.day.known_tokens} · 预留/核定责任 {budget.day.reserved_tokens} · 当日上限 {budget.daily_limit ?? "未设置"}</p>
      <p data-testid="daily-budget-completeness">{budget.day.usage_complete === true
        ? "当日用量证据完整（截至当前快照）"
        : budget.day.usage_complete === false ? "当日用量证据未完整" : "当日用量完整性未知"}
        {budget.day.accounting_resolved === true ? " · 无待核对的未知责任；活动请求可能尚未结束。" : ""}</p>
      <p>{budget.enforcement === "reactive" ? "引擎内部调用无法预先预留；通知缺失或重置时暂停核对。" : "按当前请求与输出上限估算；实际用量可能超过估算。"} 价格/币种未知。</p>
      {budget.pause_reason && <p role="status">{reasons[budget.pause_reason] ?? budget.pause_reason}</p>}
      <details>
        <summary>查看共享预算请求明细（{state?.attempts.length ?? 0} 项）</summary>
        <p>展示前 50 项；预留估算和核定责任不是测得用量。旧记录缺少归属时显示未知。</p>
        {state?.attempts.slice(0, 50).map((attempt) => <div key={attempt.id} className="mt-1 rounded border border-minimax-border p-1">
          <p>请求 {attempt.id} · {attemptStates[attempt.state] ?? attempt.state}</p>
          <p>归属运行：{attempt.run_id ? <>{attempt.run_id}{attempt.run_id === run.id ? "（当前运行）" : "（共享预算的其他运行）"}</> : "未知"}</p>
          <p>已知用量 {attempt.known_tokens ?? "未知"} · 请求预留估算 {attempt.estimated_tokens} · 核定责任 {attempt.liability_tokens ?? "未核定"}</p>
        </div>)}
      </details>
    </> : !error && <p>{state ? "该旧任务没有完整预算家族；恢复前需核对历史责任。" : "正在读取，未知用量尚未确认为零。"}</p>}
    {!!state?.day_blockers?.total && <div>
      <p>当日结果不明请求共 {state.day_blockers.total} 项，显示前 20 项；须逐项核对，核定历史日账不会清除这些请求。</p>
      {state.day_blockers.items.filter((item) => item.family_id !== budget?.family_id).map((item) => <div key={item.attempt_id}>
        <span>请求 {item.attempt_id} · 已知下界 {item.known_tokens ?? "未知"} </span>
        {item.session_id && item.run_id ? <button type="button" onClick={() => void reviewOwner(item.session_id!, item.run_id!)}>查看责任任务 {item.run_id}</button> : <span>所属任务记录缺失，请核查数据库备份。</span>}
      </div>)}
    </div>}
    {!!targets.length && <fieldset className="space-y-1 rounded border border-minimax-border p-2">
      <legend>明确核定未确认责任</legend>
      <p>核定额度仅授权记账，不会生成测得用量，也不会重置累计花费。活动请求结束后才能核对。</p>
      <label>核定 token 责任<input aria-label="核定 token 责任" type="number" min="0" value={liability} onChange={(event) => setLiability(event.target.value)} /></label>
      <label>核对依据<input aria-label="预算核对依据" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />确认依据与责任额度</label>
      {targets.map((target) => <button key={`${target.target}:${target.id}`} type="button"
        disabled={pending || active || !acknowledged || !reason.trim() || liability === "" || !Number.isSafeInteger(Number(liability)) || Number(liability) < target.minimum}
        onClick={() => void reconcile(target)}>{target.label}（至少 {target.minimum}）</button>)}
    </fieldset>}
    {error && <p role="alert">预算状态未知：{error}</p>}
  </div>;
}
