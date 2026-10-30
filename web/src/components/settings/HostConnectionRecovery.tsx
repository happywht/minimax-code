import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { tunnelPageUrl } from "../../ipc/tunnelPage";
import type { HostConnection, RemoteHost } from "../../types/ipc";
import { Button } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

function connectionUrl(connection: HostConnection): string | null {
  if (connection.state !== "connected" || !connection.local_url) return null;
  try {
    const url = new URL(connection.local_url);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.href;
  } catch { return null; }
}

/** Replace only this host's owned local forward, on an explicit action. */
export function HostConnectionRecovery({ host }: { host: RemoteHost }): JSX.Element {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [link, setLink] = useState<{ url: string; serviceId: string } | null>(null);
  const epoch = useRef(0), inFlight = useRef(false), base = ipc.agentBaseUrl;
  const signature = JSON.stringify([base, host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  const scope = useRef(signature); scope.current = signature;
  useEffect(() => { epoch.current++; setLink(null); setMessage(""); return () => { epoch.current++; }; }, [signature]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setLink(null); setMessage("当前服务连接已变化，请重新核对主机。"); }), []);
  const reconnect = async () => {
    if (inFlight.current || !host.enabled) return;
    inFlight.current = true; setBusy(true); setLink(null); setMessage("");
    const ticket = epoch.current, snapshot = signature;
    const current = () => ticket === epoch.current && scope.current === snapshot && ipc.agentBaseUrl === base;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const confirmed = await requestConfirmation({ title: "重新建立此主机的 SSH 隧道？",
        description: `将关闭并重新建立 ${host.username}@${host.hostname}:${host.port} 的本地隧道。使用旧隧道的标签页会断线；远端服务和任务不会因此被迁移或重新提交。`, confirmLabel: "重建隧道" });
      if (!confirmed || !current()) return;
      const result = await typedIPC.reconnectBoundHost({ host_id: host.id, expected_service_id: identity.service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file, remote_port: host.remote_port });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== identity.service_id) throw new Error("当前服务身份已变化");
      const address = connectionUrl(result.connection);
      if (result.connection.host_id !== host.id || result.connection.remote_port !== host.remote_port || !address) {
        setMessage("隧道未确认可用；请检查远端服务和 SSH 配置后刷新状态。"); return;
      }
      setLink({ url: address, serviceId: identity.service_id }); setMessage("隧道已建立。请在新标签页登录并核对远端服务身份及原任务记录。");
    } catch { if (current()) setMessage("隧道重建未确认；连接可能已中断，请先刷新主机状态。远端任务状态未知。"); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const open = async () => {
    if (!link || inFlight.current) return;
    // Open synchronously so browsers can attribute the popup to the click.
    const tab = window.open("about:blank", "_blank");
    if (!tab) { setMessage("浏览器阻止了新标签页，请允许弹出窗口后重试。"); return; }
    tab.opener = null;
    inFlight.current = true; setBusy(true);
    const ticket = epoch.current, snapshot = signature, expected = link;
    const current = () => ticket === epoch.current && scope.current === snapshot && ipc.agentBaseUrl === base;
    try {
      const [identity, saved, connection] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getHost(host.id), typedIPC.getHostConnection(host.id),
      ]);
      if (!current()) { tab.close(); return; }
      const h = saved.host;
      if (identity.service_id !== expected.serviceId || !h.enabled || h.id !== host.id ||
          h.hostname !== host.hostname || h.username !== host.username || h.port !== host.port ||
          h.identity_file !== host.identity_file || h.remote_port !== host.remote_port ||
          connection.connection.host_id !== host.id || connection.connection.remote_port !== host.remote_port ||
          connectionUrl(connection.connection) !== expected.url) throw new Error("connection changed");
      const after = await typedIPC.getServiceIdentity();
      if (!current() || after.service_id !== expected.serviceId) throw new Error("service changed");
      tab.location.replace(tunnelPageUrl(expected.url));
    } catch { tab.close(); if (current()) { setLink(null); setMessage("隧道或主机状态已变化，请刷新状态并重新核对。"); } }
    finally { inFlight.current = false; setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-line p-2">
    <p className="text-xs">连接中断后，可手动重建此主机的本地隧道；不会自动重启服务或重放任务。</p>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void reconnect()}>重建 SSH 隧道</Button>
    {message && <p role="status" className="text-xs">{message}</p>}
    {link && <div className="text-xs"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void open()}>核对并打开远端标签页</Button><p>登录后请确认服务身份，查看原会话和运行；连接成功不代表任务仍在运行。</p></div>}
  </div>;
}
