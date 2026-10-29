import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { DailyBudgetSettings as Settings } from "../../types/ipc";
import { Button, Input } from "../../ui";

export function DailyBudgetSettings(): JSX.Element {
  const [data, setData] = useState<Settings | null>(null);
  const [value, setValue] = useState("");
  const [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const dataRef = useRef<Settings | null>(null), dirty = useRef(false), epoch = useRef(0);
  const load = useCallback(async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setReady(false); setBusy(true); setNotice("");
    try {
      const result = await typedIPC.getDailyBudgetSettings();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (!result.service_id || result.timezone !== "Asia/Shanghai") throw new Error("预算服务身份或日界线不可确认");
      const previous = dataRef.current;
      if (!previous || previous.service_id !== result.service_id) {
        setValue(result.saved_limit === null ? "" : String(result.saved_limit)); dirty.current = false;
        if (previous) setNotice("服务身份已变化，旧服务的未保存输入已清除。");
      } else if (!dirty.current) setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      dataRef.current = result; setData(result); setReady(true);
    } catch (error) { if (ticket === epoch.current) setNotice(`预算状态未确认：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  }, []);
  useEffect(() => {
    void load();
    const off = ipc.onSideCar((event) => { ++epoch.current; setReady(false); setBusy(false);
      if (event.status === "started") void load(); else setNotice("服务连接中断；预算设置与账本状态待确认。"); });
    return () => { ++epoch.current; off(); };
  }, [load]);
  const numeric = Number(value);
  const valid = /^[1-9]\d*$/.test(value) && Number.isSafeInteger(numeric);
  const save = async (limit: number | null) => {
    if (!ready || !data || busy || (limit !== null && !valid)) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, service = data.service_id;
    setBusy(true); setNotice("");
    try {
      const result = await typedIPC.setDailyBudgetSettings(service, limit);
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (result.service_id !== service) throw new Error("服务身份已变化");
      dataRef.current = result; setData(result); dirty.current = false;
      setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      setNotice("设置已保存；接下来预算预留使用新设置。");
    } catch (error) { if (ticket === epoch.current) { setReady(false); setNotice(`保存结果未确认，请刷新核对：${error instanceof Error ? error.message : String(error)}`); } }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const day = data?.day;
  return <details className="rounded-lg border border-line bg-surface-2 p-3" data-testid="daily-budget-settings">
    <summary className="cursor-pointer text-sm font-semibold">每日 token 预算与账本</summary>
    <div className="mt-3 space-y-2 text-xs text-ink-1">
      <p>作用于此服务受账本管理的 Native/Codex 请求；新预留按当前设置检查，已在途请求不会被撤回。Codex 在下一次用量通知后按观测值处理。</p>
      <p>按 token 数量管理，不是货币金额或 Coding Plan 的费用上限。估算和外部 turn 事后控制均可能超出限额，未知用量不能视为零。</p>
      {!ready && <p role="status" className="text-status-warning">{notice || "正在确认服务与账本…"}</p>}
      {ready && data && <>
        <p>服务身份：<span className="break-all">{data.service_id}</span> · 生效日限额：{data.effective_limit === null ? "未配置" : data.effective_limit} token · 来源：{data.source === "settings" ? "界面设置" : data.source === "environment" ? "服务环境" : "未配置"}</p>
        <p>账本日期：{day?.day ?? "未知"} · 时区：Asia/Shanghai · 每日 00:00 重置日账统计。</p>
        <p>已知用量{day?.usage_complete === true ? "" : "（至少）"}：{typeof day?.known_tokens === "number" ? day.known_tokens : "未知"} · 预留/已核定责任：{typeof day?.reserved_tokens === "number" ? day.reserved_tokens : "未知"} · 未知请求：{typeof day?.unknown_attempts === "number" ? day.unknown_attempts : "未知"}</p>
        <p>{day?.usage_complete === true ? "当前日账用量已完整记录。" : "日账可能不完整或存在未测用量；请在任务预算账本核对。"} {day?.baseline_incomplete === true ? "历史基线不完整。" : day?.baseline_incomplete === false ? "" : "历史基线状态未知。"}</p>
      </>}
      <label className="flex flex-wrap items-center gap-2">日 token 上限 <Input aria-label="每日 token 上限" inputMode="numeric" value={value} maxLength={16} disabled={!ready || busy}
        onChange={(event) => { dirty.current = true; setValue(event.target.value); }} placeholder="正整数" className="w-40" /></label>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={!ready || busy || !valid} onClick={() => void save(numeric)}>保存日限额</Button>
        <Button type="button" size="sm" variant="secondary" disabled={!ready || busy} onClick={() => void save(null)}>恢复环境默认</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>刷新</Button></div>
      <p>设置保存在服务数据目录的 daily-budget-settings.json，不包含在现有数据库导出或数据包中；迁移时需单独备份。恢复环境默认可能是未设置。</p>
      {notice && ready && <p role="status">{notice}</p>}
    </div>
  </details>;
}
