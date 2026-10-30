import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostServiceStatus, RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

/** Explicit recovery of an existing user unit; never starts automatically. */
export function HostServiceStart({ host }: { host: RemoteHost }): JSX.Element {
  const [unit, setUnit] = useState("minimax-code.service");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<{ unit: string; snapshot: HostServiceStatus } | null>(null);
  const [logs, setLogs] = useState<{ unit: string; text: string; truncated: boolean } | null>(null);
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const signature = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  useEffect(() => { epoch.current++; setMessage(""); setStatus(null); setLogs(null); return () => { epoch.current++; }; }, [signature]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setStatus(null); setLogs(null); setMessage("服务连接已变化，请重新核对主机。"); }), []);

  const inspect = async () => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage(""); setStatus(null);
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
    const current = () => ticket === epoch.current && base === ipc.agentBaseUrl;
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const result = await typedIPC.getHostServiceLogs({ host_id: host.id, expected_service_id: service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, unit: requestedUnit });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== service_id) { setMessage("服务身份已变化，请重新核对。"); return; }
      if (!result.ok) { setMessage(result.reason === "timeout" ? "日志查询超时。" : "没有读到日志；可能无记录，也可能 SSH 或日志服务不可用。"); return; }
      setLogs({ unit: requestedUnit, text: result.text, truncated: result.truncated });
    } catch { if (current()) setMessage("无法查询日志，请核对主机与服务连接。"); }
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

  return <details className="rounded border border-line p-2" onToggle={(event) => { if (!event.currentTarget.open) { epoch.current++; setLogs(null); setStatus(null); setMessage(""); } }}>
    <summary>Linux 用户服务管理</summary>
    <p className="text-xs text-ink-2">使用已信任主机密钥和当前 SSH 用户执行 systemctl --user start。需要远端用户服务管理器可用；自启动设置不立即启动或停止服务，也不设置 linger 或保证机器重启后的运行。</p>
    <label className="block text-xs">服务 unit<Input aria-label={`${host.name} 服务 unit`} value={unit} disabled={busy} onChange={(event) => { setUnit(event.target.value); setStatus(null); setLogs(null); setMessage(""); }} /></label>
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
    <p className="text-xs text-ink-2">日志可能包含凭据和敏感内容，未做凭据脱敏；仅在主动请求时读取，请按需查看并谨慎分享。最多读取最近 100 条中的前 16 KiB，不自动采集或上传。</p>
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void readLogs()}>读取服务日志</Button>
      {logs && <Button type="button" size="sm" variant="secondary" onClick={() => setLogs(null)}>清除日志显示</Button>}
    </div>
    {logs && <div className="text-xs"><p>{logs.unit} 日志摘录{logs.truncated ? "（已截断）" : ""}：</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">{logs.text}</pre></div>}
    {message && <p role="status" className="break-words text-xs">{message}</p>}
  </details>;
}
