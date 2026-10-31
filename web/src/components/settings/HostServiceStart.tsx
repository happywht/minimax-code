import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostServiceLogsParams, HostServiceStatus, RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

/** Explicit recovery of an existing user unit; never starts automatically. */
export function HostServiceStart({ host }: { host: RemoteHost }): JSX.Element {
  const [unit, setUnit] = useState("minimax-code.service");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<{ unit: string; snapshot: HostServiceStatus } | null>(null);
  const [stopAck, setStopAck] = useState(false);
  const [logLines, setLogLines] = useState<NonNullable<HostServiceLogsParams["lines"]>>(100);
  const [logSince, setLogSince] = useState<NonNullable<HostServiceLogsParams["since"]>>("all");
  const [logs, setLogs] = useState<{ unit: string; text: string; truncated: boolean; serviceId: string; lines: number; since: string; readAt: string } | null>(null);
  const epoch = useRef(0);
  const inFlight = useRef(false), stopping = useRef(false);
  const signature = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  const signatureRef = useRef(signature); signatureRef.current = signature;
  useEffect(() => { epoch.current++; setMessage(""); setStatus(null); setLogs(null); setStopAck(false); return () => { epoch.current++; }; }, [signature]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setStatus(null); setLogs(null); setStopAck(false); setMessage(stopping.current ? "连接已变化，停止结果可能未知；请先核对服务及任务状态。" : "服务连接已变化，请重新核对主机。"); }), []);

  const inspect = async () => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage(""); setStatus(null); setStopAck(false);
    const ticket = epoch.current, base = ipc.agentBaseUrl, requestedUnit = unit;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl;
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const result = await typedIPC.getHostServiceStatus({ host_id: host.id, expected_service_id: service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit: requestedUnit });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== service_id) { setMessage("服务身份已变化，请重新核对。"); return; }
      if (!result.ok) { setMessage(result.reason === "timeout" ? "查询超时，服务状态未知。" : "无法读取服务状态；请检查 SSH、主机密钥、用户服务管理器及 unit。服务状态未知。"); return; }
      setStatus({ unit: requestedUnit, snapshot: result.status });
    } catch { if (current()) setMessage("查询失败，服务状态未知；请核对主机配置与连接后重试。"); }
    finally { inFlight.current = false; setBusy(false); }
  };

  const readLogs = async () => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage(""); setLogs(null);
    const ticket = epoch.current, base = ipc.agentBaseUrl, requestedUnit = unit;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl && signatureRef.current === signature;
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const result = await typedIPC.getHostServiceLogs({ host_id: host.id, expected_service_id: service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit: requestedUnit, lines: logLines, since: logSince });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== service_id) { setMessage("服务身份已变化，请重新核对。"); return; }
      if (!result.ok) { setMessage(result.reason === "timeout" ? "日志查询超时。" : "没有读到日志；可能无记录，也可能 SSH 或日志服务不可用。"); return; }
      setLogs({ unit: requestedUnit, text: result.text, truncated: result.truncated, serviceId: service_id, lines: logLines, since: logSince, readAt: new Date().toISOString() });
    } catch { if (current()) setMessage("无法查询日志，请核对主机与服务连接。"); }
    finally { inFlight.current = false; setBusy(false); }
  };

  const downloadLogs = async () => {
    if (inFlight.current || !logs || !host.enabled) return;
    const selected = logs, ticket = epoch.current, base = ipc.agentBaseUrl, expectedHost = signature;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl && signatureRef.current === expectedHost;
    inFlight.current = true; setBusy(true); setMessage("");
    try {
      const accepted = await requestConfirmation({ title: "下载服务日志摘录？", description: "文件将保存到浏览器的下载位置，包含当前显示的日志和主机信息，可能含凭据或其他敏感内容；日志未脱敏且可能截断。请自行安全保存和分享。", confirmLabel: "下载摘录" });
      if (!accepted || !current()) return;
      const [identity, saved] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getHost(host.id)]);
      if (!current()) return;
      const remote = saved.host;
      const savedSignature = JSON.stringify([remote.id, remote.hostname, remote.username, remote.port, remote.identity_file, remote.remote_port, remote.enabled]);
      if (identity.service_id !== selected.serviceId || savedSignature !== expectedHost) throw new Error("主机或服务身份已变化");
      const heading = `主机: ${host.username}@${host.hostname}:${host.port}\n服务: ${selected.unit}\n服务身份: ${selected.serviceId}\n读取时刻 UTC: ${selected.readAt}\n范围: ${selected.since}; 最近最多 ${selected.lines} 条\n16 KiB 截断: ${selected.truncated ? "是" : "否"}\n日志已过滤部分控制字符，未做凭据脱敏。\n\n`;
      const url = URL.createObjectURL(new Blob([heading, selected.text], { type: "text/plain;charset=utf-8" }));
      try {
        const link = document.createElement("a"); link.href = url; link.download = `${selected.unit}-journal.txt`; link.click();
      } finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
      setMessage("已请求浏览器下载当前摘录；请检查下载结果并妥善保管。");
    } catch { if (current()) setMessage("无法下载摘录，请重新核对主机和服务身份。"); }
    finally { inFlight.current = false; setBusy(false); }
  };

  const setAutostart = async (enabled: boolean) => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage(""); setStatus(null);
    const ticket = epoch.current, base = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl;
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const accepted = await requestConfirmation({
        title: enabled ? "启用用户服务自启动？" : "停用用户服务自启动？",
        description: enabled
          ? `将在 ${host.username}@${host.hostname}:${host.port} 启用 ${unit} 的用户服务自启动。这可能在未来启动服务及定时任务；不会立即启动服务或配置 linger。`
          : `将在 ${host.username}@${host.hostname}:${host.port} 停用 ${unit} 的用户服务自启动；不会停止正在运行的服务或取消任务。`,
        confirmLabel: enabled ? "启用自启动" : "停用自启动",
      });
      if (!accepted || !current()) return;
      const result = await typedIPC.setHostServiceAutostart({ host_id: host.id, expected_service_id: service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit, enabled });
      if (!current()) return;
      setMessage(result.ok ? "命令已返回成功。请查询服务状态核对 unit 文件启用状态；用户管理器与重启行为仍须单独核对。" :
        "操作未确认，结果可能未知；请先查询服务状态再决定是否重试。");
    } catch { if (current()) setMessage("操作未确认；如果请求已发出，结果可能未知。请先查询状态再决定是否重试。"); }
    finally { inFlight.current = false; setBusy(false); }
  };

  const stop = async () => {
    if (inFlight.current || !host.enabled || !stopAck || !status || status.unit !== unit || status.snapshot.LoadState !== "loaded") return;
    inFlight.current = true; setBusy(true); setMessage("");
    const ticket = epoch.current, base = ipc.agentBaseUrl, requestedUnit = unit;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const accepted = await requestConfirmation({ title: "停止远端用户服务？", description: `将在 ${host.username}@${host.hostname}:${host.port} 停止 ${requestedUnit}。正在运行、排队或清理的任务可能被中断，连接可能断开；请先核对并保存需要的结果。此操作不禁用自启动或重新启动服务。`, confirmLabel: "确认停止" });
      if (!accepted || !current()) return;
      setStatus(null); setStopAck(false); setLogs(null); stopping.current = true;
      setMessage("停止请求处理中；请勿重复提交。连接中断时结果可能未知。");
      const result = await typedIPC.stopHostService({ host_id: host.id, expected_service_id: identity.service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit: requestedUnit, acknowledge_interrupt: true });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== identity.service_id) throw new Error("服务身份已变化");
      setMessage(result.ok ? "停止命令已返回成功。请重新查询服务状态并核对原任务；命令成功不代表任务已完成或清理已结束。" :
        "停止未确认，结果可能未知；请先查询服务和任务状态，不要盲目重试。");
    } catch { if (current()) setMessage("停止未确认；请求可能已发出，请先核对服务及任务状态。"); }
    finally { stopping.current = false; inFlight.current = false; setBusy(false); }
  };

  const start = async () => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage(""); setStatus(null);
    const ticket = epoch.current, base = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl;
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const accepted = await requestConfirmation({ title: "启动远端用户服务？", description: `将在 ${host.username}@${host.hostname}:${host.port} 上启动 ${unit}。这可能触发该服务的定时任务；请确认主机和 unit。不会安装、启用或重启服务。`, confirmLabel: "启动服务" });
      if (!accepted || !current()) return;
      const result = await typedIPC.startHostService({ host_id: host.id, expected_service_id: service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit });
      if (!current()) return;
      setMessage(result.ok ? "启动命令已返回成功。请重新连接隧道并在远端核对服务与任务状态。" :
        `启动未确认${result.timed_out ? "（超时，结果可能未知）" : ""}：${result.stderr || "请核对远端服务状态"}`);
    } catch (error) { if (current()) setMessage(`${String(error)}；如果请求已发出，结果可能未知，请先核对远端状态。`); }
    finally { inFlight.current = false; setBusy(false); }
  };

  return <details className="rounded border border-line p-2" onToggle={(event) => { if (!event.currentTarget.open) { epoch.current++; setLogs(null); setStatus(null); setStopAck(false); setMessage(""); } }}>
    <summary>Linux 用户服务管理</summary>
    <p className="text-xs text-ink-2">使用已信任主机密钥和当前 SSH 用户执行 systemctl --user start。</p><p className="text-xs text-ink-2">需要远端用户服务管理器可用；</p><p className="text-xs text-ink-2">自启动设置不立即启动或停止服务，也不设置 linger 或保证机器重启后的运行。</p>
    <label className="block text-xs">服务 unit<Input aria-label={`${host.name} 服务 unit`} value={unit} disabled={busy} onChange={(event) => { setUnit(event.target.value); setStatus(null); setLogs(null); setStopAck(false); setMessage(""); }} /></label>
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void inspect()}>查询服务状态</Button>
      <Button type="button" size="sm" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void start()}>启动服务</Button>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void setAutostart(true)}>启用自启动</Button>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void setAutostart(false)}>停用自启动</Button>
    </div>
    {status && <div className="text-xs" role="status">
      <p>读取时的 {status.unit} 状态：{status.snapshot.LoadState} / {status.snapshot.ActiveState} / {status.snapshot.SubState}</p>
      <p>服务结果：{status.snapshot.Result} · 主进程退出状态：{status.snapshot.ExecMainStatus} · 主进程 PID：{status.snapshot.MainPID} · unit 文件：{status.snapshot.UnitFileState ?? "未知"}</p>
      <p>这是一次服务快照，不表示任务成功或服务持续健康；不读取日志。</p>
    </div>}
    <div className="space-y-1 rounded border border-line p-2 text-xs">
      <p>停止服务可能中断正在执行、排队或清理的任务。先查询服务状态并自行核对任务；状态快照不能证明服务空闲。</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={stopAck} disabled={busy || !status || status.unit !== unit || status.snapshot.LoadState !== "loaded"} onChange={(event) => setStopAck(event.target.checked)} />我已核对目标主机和 unit，理解停止可能中断任务。</label>
      <Button type="button" size="sm" variant="danger" disabled={busy || !host.enabled || !stopAck || !status || status.unit !== unit || status.snapshot.LoadState !== "loaded"} onClick={() => void stop()}>停止服务</Button>
    </div>
    <p className="text-xs text-ink-2">日志可能包含凭据和敏感内容，未做凭据脱敏；仅在主动请求时读取，请按需查看并谨慎分享。</p><p className="text-xs text-ink-2">读取所选范围最近条目中的前 16 KiB，截断时可能不含最新条目；不自动采集或上传。</p>
    <div className="flex flex-wrap gap-2 text-xs">
      <label>最近条数<select aria-label="日志最近条数" className="block rounded border border-line bg-surface-2 p-1" disabled={busy} value={logLines} onChange={(event) => { setLogLines(Number(event.target.value) as NonNullable<HostServiceLogsParams["lines"]>); setLogs(null); }}><option value="25">25 条</option><option value="50">50 条</option><option value="100">100 条</option><option value="200">200 条</option></select></label>
      <label>时间范围<select aria-label="日志时间范围" className="block rounded border border-line bg-surface-2 p-1" disabled={busy} value={logSince} onChange={(event) => { setLogSince(event.target.value as NonNullable<HostServiceLogsParams["since"]>); setLogs(null); }}><option value="all">不限时间</option><option value="15m">最近 15 分钟</option><option value="1h">最近 1 小时</option><option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option></select></label>
    </div>
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void readLogs()}>读取服务日志</Button>
      {logs && <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void downloadLogs()}>下载当前摘录</Button>}
      {logs && <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => setLogs(null)}>清除日志显示</Button>}
    </div>
    {logs && <div className="text-xs"><p>{logs.unit} 日志摘录 · {logs.since} · 最近 {logs.lines} 条 · 读取于 {logs.readAt}{logs.truncated ? "（已截断）" : ""}：</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">{logs.text}</pre></div>}
    {message && <p role="status" className="break-words text-xs">{message}</p>}
  </details>;
}
