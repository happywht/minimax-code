import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useScheduleStore } from "../../stores";
import type { ScheduledJob, ScheduleTriggerResult } from "../../types/ipc";
import { Button, Textarea } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

type Intent = { version: 1; job_id: string; service_id: string; revision: number; key: string };
const valid = (value: unknown): value is Intent => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Intent;
  return v.version === 1 && typeof v.job_id === "string" && !!v.job_id && typeof v.service_id === "string" && !!v.service_id &&
    Number.isSafeInteger(v.revision) && v.revision > 0 && typeof v.key === "string" && /^[0-9a-f-]{36}$/.test(v.key);
};

export function ScheduleManualTrigger({ job, serviceId }: { job: ScheduledJob; serviceId?: string }): JSX.Element {
  const base = ipc.agentBaseUrl;
  const storageKey = `minimax:schedule-manual:${JSON.stringify([base, serviceId, job.id])}`;
  const read = (): Intent | null | "unavailable" => {
    try { const raw = sessionStorage.getItem(storageKey); if (!raw) return null;
      const value: unknown = JSON.parse(raw);
      return valid(value) && value.job_id === job.id && value.service_id === serviceId ? value : "unavailable";
    } catch { return "unavailable"; }
  };
  const [pending, setPending] = useState<Intent | null | "unavailable">(() => read());
  const [input, setInput] = useState(""), [message, setMessage] = useState("");
  const [receipt, setReceipt] = useState<ScheduleTriggerResult | null>(null), [busy, setBusy] = useState(false);
  const epoch = useRef(0), inFlight = useRef(false);
  const currentScope = JSON.stringify([base, serviceId, job.id]);
  const scopeRef = useRef(currentScope); scopeRef.current = currentScope;
  useEffect(() => { epoch.current++; setPending(read()); setInput(""); setReceipt(null); setMessage("");
    return () => { epoch.current++; }; }, [storageKey]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setReceipt(null); setMessage("连接已变化；请重新核对保存的请求和计划历史。"); }), []);
  const current = (ticket: number, scope: string) => epoch.current === ticket && scopeRef.current === scope && ipc.agentBaseUrl === base;

  const submit = async (intent: Intent, retry: boolean) => {
    if (inFlight.current || !serviceId || intent.service_id !== serviceId || intent.job_id !== job.id) return;
    inFlight.current = true; setBusy(true); setMessage(""); setReceipt(null);
    const ticket = epoch.current, scope = currentScope;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current(ticket, scope)) return;
      if (identity.service_id !== intent.service_id) throw new Error("服务身份已变化");
      const accepted = await requestConfirmation({ title: retry ? "重试同一立即运行请求？" : "立即运行此计划？",
        description: retry ? "将使用原请求键和原计划版本。若原请求没有接单，此操作可能首次创建运行；请先核对计划历史。" : `将在当前服务上按计划版本 ${intent.revision} 创建一次运行；接单不代表任务完成。`,
        confirmLabel: retry ? "重试原请求" : "确认运行" });
      if (!accepted || !current(ticket, scope)) return;
      // Persist before dispatch; do not overwrite another pending request.
      const existing = read();
      if (existing === "unavailable" || (existing && JSON.stringify(existing) !== JSON.stringify(intent))) throw new Error("另一条待确认请求已存在");
      sessionStorage.setItem(storageKey, JSON.stringify(intent));
      setPending(intent);
      const result = await typedIPC.runNowJob(intent.job_id, intent.key, intent.revision, intent.service_id);
      if (!current(ticket, scope)) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current(ticket, scope)) return;
      if (after.service_id !== intent.service_id || result.job_id !== intent.job_id || !result.occurrence_id) throw new Error("回执归属不匹配");
      sessionStorage.removeItem(storageKey); setPending(null); setReceipt(result);
      setMessage(result.created ? "已接收一次运行请求；请查看发生记录和运行结果。" : "已找到原请求的发生记录；未新建一次运行。");
      await useScheduleStore.getState().refresh();
    } catch { if (current(ticket, scope)) { setPending(read()); setMessage("请求未确认；请先核对历史，必要时使用原请求键重试，不要另发新请求。"); } }
    finally { inFlight.current = false; setBusy(false); }
  };
  const begin = async () => {
    if (busy || pending || !serviceId || !job.revision || !job.enabled || job.binding_state !== "ready" || job.active_occurrence_id) return;
    if (read()) { setPending(read()); return; }
    await submit({ version: 1, job_id: job.id, service_id: serviceId, revision: job.revision, key: crypto.randomUUID() }, false);
  };
  const importRecord = () => {
    if (busy || pending || !serviceId || input.length > 4096) return;
    try {
      const value: unknown = JSON.parse(input);
      if (!valid(value) || value.service_id !== serviceId || value.job_id !== job.id) throw new Error("记录与当前服务或计划不匹配");
      sessionStorage.setItem(storageKey, JSON.stringify(value)); setPending(value); setInput("");
      setMessage("已保存原请求；尚未触发。请先核对历史，再决定是否重试。");
    } catch { setMessage("恢复记录无效、不匹配或本标签页存储不可用；未触发运行。"); }
  };
  const discard = async () => {
    if (!pending || busy) return;
    const ticket = epoch.current, scope = currentScope;
    const accepted = await requestConfirmation({ title: "清除待确认请求？", description: "只清除本标签页记录，不取消可能已接单的运行。请先核对计划历史并自行保存请求记录，避免重复执行。", confirmLabel: "清除记录" });
    if (!accepted || !current(ticket, scope)) return;
    try { const existing = read();
      if (JSON.stringify(existing) !== JSON.stringify(pending)) throw new Error("记录已变化");
      sessionStorage.removeItem(storageKey); setPending(null); setMessage("已清除本地记录，未取消远端运行。"); }
    catch { setMessage("无法清除本地记录，请检查浏览器存储。"); }
  };
  return <div className="space-y-1 px-3 py-2 text-xs">
    <Button type="button" size="sm" data-testid={`settings-job-run-now-${job.id}`} disabled={busy || !!pending || !serviceId || !job.revision || !job.enabled || job.binding_state !== "ready" || !!job.active_occurrence_id} onClick={() => void begin()}>立即运行</Button>
    {pending === "unavailable" && <div><p role="alert">浏览器存储不可用或记录损坏；无法安全发起新请求。请先检查原记录及计划历史。</p><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void discard()}>清除损坏记录</Button></div>}
    {pending && pending !== "unavailable" && <div className="space-y-1"><p>有待确认的立即运行请求；请先核对计划历史。</p>
      <Textarea aria-label={`${job.name} 立即运行恢复记录`} readOnly rows={4} value={JSON.stringify(pending)} />
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void submit(pending, true)}>重试原请求</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void discard()}>清除本地记录</Button></div></div>}
    <details><summary>导入立即运行恢复记录</summary><p>从原标签页复制记录后粘贴；仅匹配服务身份和计划 ID，不自动运行。数据库克隆可能复用服务身份，请人工确认远端主机。</p>
      <Textarea aria-label={`${job.name} 导入立即运行记录`} rows={3} value={input} disabled={busy || !!pending} onChange={(event) => setInput(event.target.value)} />
      <Button type="button" size="sm" variant="secondary" disabled={busy || !!pending || !input.trim()} onClick={importRecord}>填入恢复记录</Button></details>
    {receipt && <p className="break-all">发生记录：{receipt.occurrence_id} · 接单时状态：{receipt.status}</p>}
    {message && <p role="status">{message}</p>}
  </div>;
}
