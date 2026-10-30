import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { BudgetStateResult } from "../../types/ipc";
import { Button } from "../../ui";

const reasons: Record<string, string> = {
  unresolved_attempt: "存在结果不明的模型请求", legacy_family_incomplete: "旧任务累计用量不完整",
  daily_accounting_incomplete: "当日历史或请求责任不完整", family_ceiling: "任务累计额度已达上限",
  daily_ceiling: "本机今日额度已达上限", budget_storage_unavailable: "预算存储或恢复失败",
};

/** Read-only project view of a paused run's shared ledger. */
export function ProjectRunBudget({ host, service, project, session, run }: {
  host: string; service: string; project: string; session: string; run: string;
}): JSX.Element {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const [state, setState] = useState<BudgetStateResult | null>(null);
  const [cursor, setCursor] = useState(0), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const load = useCallback(async (day?: string, after = 0) => {
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
    setBusy(true); setError(""); setState(null);
    try {
      const [identity, snapshot, owner, budget] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getRunSteps(run), typedIPC.getSession(session),
        typedIPC.getBudget(run, day, after || undefined),
      ]);
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (!service || identity.service_id !== service || finalIdentity.service_id !== service ||
          snapshot.run.id !== run || snapshot.run.session_id !== session || snapshot.run.metadata?.project_id !== project || snapshot.run.status !== "budget_paused" ||
          owner.session.id !== session || owner.session.project_id !== project ||
          !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
          snapshot.steps.some((step) => step.run_id !== run || step.session_id !== session) ||
          (day !== undefined && budget.day.day !== day)) throw new Error("服务、运行、会话或账本日期已变化，请重新选择任务。");
      setState(budget); setCursor(after);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setBusy(false); }
  }, [host, service, project, session, run, activeSession]);
  useEffect(() => {
    void load();
    const off = ipc.onSideCar(() => { ++epoch.current; setState(null); setBusy(false); setError("连接已变化，请刷新账本。"); });
    return () => { ++epoch.current; off(); };
  }, [load]);
  const budget = state?.budget, blockers = state?.day_blockers;
  const next = blockers?.next_cursor;
  return <section className="mt-2 space-y-2 rounded border border-line p-2 text-xs" aria-label="预算暂停账本">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">预算暂停：共享账本</h4>
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void load()}>刷新账本</Button></div>
    {busy && <p role="status">正在核对身份并读取账本…</p>}
    {error && <p role="alert" className="break-words text-status-warning">账本未确认：{error}</p>}
    {state && <>
      {budget ? <>
        <p>累计家族上限：{budget.limit_tokens ?? "未设置"} · 已知用量{budget.usage_complete === true ? "" : "至少"} {budget.known_tokens} · 预留/核定责任 {budget.reserved_tokens} · 剩余估算 {budget.remaining_estimate ?? "未知或未设置"}</p>
        <p>结果不明请求：{budget.unknown_attempts} · 历史基线：{budget.baseline_incomplete ? "不完整，需核对" : "账本未标记不完整"} · 暂停原因：{budget.pause_reason ? reasons[budget.pause_reason] ?? budget.pause_reason : "未记录"}</p>
        <p>共享家族包含父子任务与恢复运行；已知值、估算和核定责任不是同一种用量。价格和币种未知。</p>
        <details><summary>共享家族请求明细（已返回 {state.attempts.length} 项）</summary>
          <p>旧记录可能缺少归属；此列表有服务端返回数量限制。</p>
          <ul className="max-h-48 space-y-1 overflow-auto">{state.attempts.map((attempt) => <li key={attempt.id} className="break-all rounded bg-surface-1 p-1">
            请求 {attempt.id} · 运行 {attempt.run_id ?? "未知"} · 状态 {attempt.state} · 已知 {attempt.known_tokens ?? "未知"} · 预留估算 {attempt.estimated_tokens} · 核定责任 {attempt.liability_tokens ?? "未核定"}
          </li>)}</ul>
        </details>
      </> : <p>此运行没有可确认的预算家族记录；历史责任不能视为零。</p>}
      <p>本机账本日 {state.day.day} · 已知用量{state.day.usage_complete === true ? "" : "至少"} {state.day.known_tokens} · 预留/核定责任 {state.day.reserved_tokens} · 不明请求 {state.day.unknown_attempts} · 历史基线 {state.day.baseline_incomplete ? "不完整" : "未标记不完整"}</p>
      <p>当日用量完整性：{state.day.usage_complete === true ? "完整（读取时快照）" : state.day.usage_complete === false ? "不完整" : "未知"}；当日上限：{budget?.daily_limit ?? "未知或未设置"}。</p>
      {blockers ? <div className="space-y-1"><p>当日不明请求：{blockers.total} 项；每页最多 20 项。记录可能在翻页时变化。</p>
        {blockers.items.map((item) => <p key={item.attempt_id} className="break-all">请求 {item.attempt_id} · 家族 {item.family_id} · 运行 {item.run_id ?? "未知"} · 会话 {item.session_id ?? "未知"} · 已知下界 {item.known_tokens ?? "未知"}</p>)}
        <div className="flex gap-2">{cursor > 0 && <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>返回第一页</Button>}
          {next != null && Number.isSafeInteger(next) && next > cursor && <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void load(state.day.day, next)}>查看后续不明请求</Button>}</div>
      </div> : <p>当日不明请求明细未提供，不能视为没有阻塞。</p>}
      <p className="text-ink-2">此处只读取账本。需核定责任时请进入所属会话的预算账本；恢复仍需下方明确核对并确认，不会自动运行。</p>
    </>}
  </section>;
}
