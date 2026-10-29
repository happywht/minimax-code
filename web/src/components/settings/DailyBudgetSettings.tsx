import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { DailyBudgetSettings as Settings } from "../../types/ipc";
import { Button, Input } from "../../ui";

type ImportedBudget = { sourceService: string; limit: number | null; targetHost: string; targetService: string;
  expectedSaved: number | null; currentInput: string };

export function DailyBudgetSettings(): JSX.Element {
  const [data, setData] = useState<Settings | null>(null);
  const [value, setValue] = useState("");
  const [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [candidate, setCandidate] = useState<ImportedBudget | null>(null);
  const valueRef = useRef(value); valueRef.current = value;
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
        setValue(result.saved_limit === null ? "" : String(result.saved_limit)); dirty.current = false; setCandidate(null);
        if (previous) setNotice("服务身份已变化，旧服务的未保存输入已清除。");
      } else if (!dirty.current) setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      dataRef.current = result; setData(result); setReady(true);
    } catch (error) { if (ticket === epoch.current) setNotice(`预算状态未确认：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  }, []);
  useEffect(() => {
    void load();
    const off = ipc.onSideCar((event) => { ++epoch.current; setReady(false); setBusy(false); setCandidate(null);
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
      const result = await typedIPC.setDailyBudgetSettings(service, limit, data.saved_limit);
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (result.service_id !== service) throw new Error("服务身份已变化");
      dataRef.current = result; setData(result); dirty.current = false; setCandidate(null);
      setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      setNotice("设置已保存；接下来预算预留使用新设置。");
    } catch (error) { if (ticket === epoch.current) { setReady(false); setNotice(`保存结果未确认，请刷新核对：${error instanceof Error ? error.message : String(error)}`); } }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const exportSettings = async () => {
    if (!ready || !data || busy) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, service = data.service_id;
    setBusy(true); setNotice("");
    try {
      const result = await typedIPC.getDailyBudgetSettings();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (result.service_id !== service) throw new Error("服务身份已变化");
      const json = JSON.stringify({ format: "minimax-daily-budget", version: 1,
        source_service_id: service, saved_limit: result.saved_limit }, null, 2);
      const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `daily-budget-${crypto.randomUUID()}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      dataRef.current = result; setData(result);
      if (!dirty.current) setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      setNotice("已交给浏览器下载；仅包含已保存的设置值和来源服务身份，不含账本或环境变量。");
    } catch (error) { if (ticket === epoch.current) { setReady(false); setNotice(`无法确认导出设置：${error instanceof Error ? error.message : String(error)}`); } }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const readImport = async (file: File) => {
    if (!ready || !data || busy) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, service = data.service_id, originalInput = valueRef.current;
    setBusy(true); setCandidate(null); setNotice("");
    try {
      if (file.size > 4096) throw new Error("文件超过 4 KiB");
      const raw = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("格式无效");
      const row = parsed as Record<string, unknown>;
      if (Object.keys(row).sort().join(",") !== "format,saved_limit,source_service_id,version" || row.format !== "minimax-daily-budget" || row.version !== 1 ||
          typeof row.source_service_id !== "string" || !row.source_service_id || row.source_service_id.length > 128 ||
          (row.saved_limit !== null && (typeof row.saved_limit !== "number" || !Number.isSafeInteger(row.saved_limit) || row.saved_limit <= 0))) throw new Error("格式或限额无效");
      const current = await typedIPC.getDailyBudgetSettings();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host || valueRef.current !== originalInput) return;
      if (current.service_id !== service) throw new Error("目标服务身份已变化");
      dataRef.current = current; setData(current);
      const currentInput = dirty.current ? originalInput : current.saved_limit === null ? "" : String(current.saved_limit);
      if (!dirty.current) setValue(currentInput);
      setCandidate({ sourceService: row.source_service_id, limit: row.saved_limit as number | null, targetHost: host,
        targetService: service, expectedSaved: current.saved_limit, currentInput });
      setNotice("已读取文件，请核对目标和替换内容；尚未导入。");
    } catch (error) { if (ticket === epoch.current) setNotice(`未读取设置文件：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const applyImport = async () => {
    if (!candidate || !ready || busy || !data || ipc.agentBaseUrl !== candidate.targetHost ||
        data.service_id !== candidate.targetService || data.saved_limit !== candidate.expectedSaved || valueRef.current !== candidate.currentInput) {
      setCandidate(null); setNotice("当前设置或输入已变化，请重新选择文件。"); return;
    }
    const text = candidate.limit === null ? "恢复目标服务的环境默认，可能没有日限额" : `替换为 ${candidate.limit} token`;
    if (!window.confirm(`将覆盖目标服务当前保存的每日预算设置，${text}。已在途请求不会撤回。确定继续？`)) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const result = await typedIPC.setDailyBudgetSettings(candidate.targetService, candidate.limit, candidate.expectedSaved);
      if (ticket !== epoch.current || ipc.agentBaseUrl !== candidate.targetHost) return;
      if (result.service_id !== candidate.targetService) throw new Error("目标服务身份已变化");
      dataRef.current = result; setData(result); dirty.current = false; setCandidate(null);
      setValue(result.saved_limit === null ? "" : String(result.saved_limit));
      setNotice("每日预算设置已导入；账本与环境变量未更改。");
    } catch (error) { if (ticket === epoch.current) { setReady(false); setCandidate(null); setNotice(`导入未确认，请刷新核对：${error instanceof Error ? error.message : String(error)}`); } }
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
        onChange={(event) => { dirty.current = true; setCandidate(null); setValue(event.target.value); }} placeholder="正整数" className="w-40" /></label>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={!ready || busy || !valid} onClick={() => void save(numeric)}>保存日限额</Button>
        <Button type="button" size="sm" variant="secondary" disabled={!ready || busy} onClick={() => void save(null)}>恢复环境默认</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>刷新</Button></div>
      <div className="space-y-2 rounded border border-line p-2">
        <p className="font-medium">单独导出或导入每日设置</p>
        <p>只包含界面保存的限额和来源服务身份，不含账本、用量、密钥或环境变量；不属于现有数据库导出或数据包。</p>
        <div className="flex flex-wrap items-center gap-2"><Button type="button" size="sm" variant="secondary" disabled={!ready || busy} onClick={() => void exportSettings()}>导出已保存设置</Button>
          <label>选择设置文件 <input aria-label="选择每日预算设置文件" type="file" accept=".json,application/json" disabled={!ready || busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void readImport(file); }} /></label></div>
        {candidate && <div className="space-y-1 rounded bg-surface-1 p-2"><p className="break-all">来源服务：{candidate.sourceService} · 目标服务：{candidate.targetService} · 地址：{candidate.targetHost}</p>
          <p>目标当前保存值：{candidate.expectedSaved ?? "无（使用环境默认）"}；导入后：{candidate.limit ?? "无（使用目标环境默认，可能未设置）"}。</p>
          <p>可跨服务手动导入；导入会替换目标已保存设置，未保存的上方输入也会被替换。账本、在途请求和环境变量不随文件改变。</p>
          <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy || !ready} onClick={() => void applyImport()}>确认导入目标服务</Button>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { setCandidate(null); setNotice("已取消导入。"); }}>取消</Button></div></div>}
      </div>
      <p>设置保存在服务数据目录的 daily-budget-settings.json，不包含在现有数据库导出或数据包中。恢复环境默认可能是未设置。</p>
      {notice && ready && <p role="status">{notice}</p>}
    </div>
  </details>;
}
