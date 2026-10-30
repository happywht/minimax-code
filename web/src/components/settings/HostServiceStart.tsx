import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

/** Explicit recovery of an existing user unit; never starts automatically. */
export function HostServiceStart({ host }: { host: RemoteHost }): JSX.Element {
  const [unit, setUnit] = useState("minimax-code.service");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const signature = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  useEffect(() => { epoch.current++; setMessage(""); return () => { epoch.current++; }; }, [signature]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setMessage("服务连接已变化，请重新核对主机。"); }), []);

  const start = async () => {
    if (inFlight.current || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)) return;
    inFlight.current = true; setBusy(true); setMessage("");
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

  return <details className="rounded border border-line p-2">
    <summary>启动已安装的 Linux 用户服务</summary>
    <p className="text-xs text-ink-2">使用已信任主机密钥和当前 SSH 用户执行 systemctl --user start。需要远端用户服务管理器可用；不设置 linger，也不保证机器重启后的运行。</p>
    <label className="block text-xs">服务 unit<Input aria-label={`${host.name} 服务 unit`} value={unit} disabled={busy} onChange={(event) => setUnit(event.target.value)} /></label>
    <Button type="button" size="sm" disabled={busy || !host.enabled || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$/.test(unit)} onClick={() => void start()}>启动服务</Button>
    {message && <p role="status" className="break-words text-xs">{message}</p>}
  </details>;
}
