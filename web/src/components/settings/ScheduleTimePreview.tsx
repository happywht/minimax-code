import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { SchedulePreview } from "../../types/ipc";
import { Button } from "../../ui";

function displayTime(value: string, timezone: string): string {
  try {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return value;
    return `${new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "short" }).format(date)}（${value}）`;
  } catch { return `${value}（浏览器无法显示计划时区）`; }
}

/** Calculate future due instants on the current service without touching a job. */
export function ScheduleTimePreview({ cron, timezone, serviceId }: { cron: string; timezone: string; serviceId?: string | null }): JSX.Element {
  const [result, setResult] = useState<SchedulePreview | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const base = ipc.agentBaseUrl;
  const scope = JSON.stringify([base, serviceId, cron, timezone]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  useEffect(() => { epoch.current++; setResult(null); setMessage(""); setBusy(false); return () => { epoch.current++; }; }, [scope]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setResult(null); setBusy(false); setMessage("连接已变化，请重新预览。"); }), []);
  const preview = async () => {
    if (!serviceId || busy || !cron.trim() || !timezone.trim()) return;
    const ticket = ++epoch.current, snapshot = scope;
    const current = () => ticket === epoch.current && scopeRef.current === snapshot && ipc.agentBaseUrl === base;
    setBusy(true); setResult(null); setMessage("");
    try {
      const response = await typedIPC.previewSchedule(cron, timezone, serviceId);
      if (!current()) return;
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== serviceId || response.service_id !== serviceId || response.cron_expr !== cron || response.timezone !== timezone || !Array.isArray(response.times) || response.times.length > 5 || !response.times.every((time) => typeof time === "string" && Number.isFinite(Date.parse(time)))) throw new Error("服务或预览回执已变化");
      setResult(response);
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : "无法计算到期时间"); }
    finally { if (current()) setBusy(false); }
  };
  return <div className="my-2 space-y-1 text-xs">
    <Button type="button" size="sm" variant="secondary" disabled={busy || !serviceId || !cron.trim() || !timezone.trim()} onClick={() => void preview()}>{busy ? "计算中…" : "预览未来到期时间"}</Button>
    <p className="text-ink-2">按当前服务的 Cron 规则和计划时区计算，最多 5 次；不会创建或触发计划。</p><p className="text-ink-2">停用、错过周期、服务停机及执行准入等会影响实际运行。</p>
    {message && <p role="status" className="break-words text-status-error">{message}</p>}
    {result && <div role="status"><p>计算时刻：{result.calculated_at}；计划时区：{result.timezone}</p>
      {result.times.length ? <ol className="list-inside list-decimal">{result.times.map((time) => <li key={time}>{displayTime(time, result.timezone)}</li>)}</ol> : <p>没有可计算的未来到期时间。</p>}</div>}
  </div>;
}
