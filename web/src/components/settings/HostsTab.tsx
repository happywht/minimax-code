import { useCallback, useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostConnection, RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { HostFileTransfer } from "./HostFileTransfer";
import { tunnelPageUrl } from "../../ipc/tunnelPage";

const STATUS_LABELS: Record<HostConnection["state"], string> = {
  connected: "已连接", degraded: "SSH 隧道存在，远端服务不可用", disconnected: "已断开", connecting: "连接中",
  reconnecting: "正在重连", failed: "连接失败",
};

// Only expose the loopback HTTP endpoint returned by the tunnel manager.
function tunnelUrl(connection?: HostConnection): string | null {
  if (connection?.state !== "connected" || !connection.local_url) return null;
  try {
    const url = new URL(connection.local_url);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.href;
  } catch { return null; }
}

export function HostsTab(): JSX.Element {
  const [hosts, setHosts] = useState<RemoteHost[]>([]);
  const [connections, setConnections] = useState<Record<string, HostConnection>>({});
  const [error, setError] = useState<string | null>(null);
  const [connectionEpoch, setConnectionEpoch] = useState(0);
  const scopeVersion = useRef(0);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [username, setUsername] = useState("");
  const [port, setPort] = useState("22");
  const [identity, setIdentity] = useState("");
  const [remotePort, setRemotePort] = useState("8765");
  const [hostPorts, setHostPorts] = useState<Record<string, string>>({});
  const [editId, setEditId] = useState<string | null>(null);
  const [edit, setEdit] = useState({ name: "", hostname: "", username: "", port: "22", identity_file: "", remote_port: "8765" });

  const refresh = useCallback(async () => {
    const version = scopeVersion.current;
    setBusy(true); setError(null);
    try {
      const result = await typedIPC.listHosts();
      if (version !== scopeVersion.current) return;
      setHosts(result.hosts);
      setHostPorts((current) => Object.fromEntries(result.hosts.map((host) => [host.id, current[host.id] ?? String(host.remote_port)])));
      const statuses = await Promise.all(result.hosts.map(async (host) => {
        try { return [host.id, (await typedIPC.getHostConnection(host.id)).connection] as const; }
        catch { return null; }
      }));
      if (version === scopeVersion.current) setConnections(Object.fromEntries(statuses.filter((item) => item !== null)));
    } catch (err) { if (version === scopeVersion.current) { setConnections({}); setError(String(err)); } }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => ipc.onSideCar((event) => {
    scopeVersion.current++;
    setConnections({});
    if (event.status === "started") { setConnectionEpoch((value) => value + 1); void refresh(); }
  }), [refresh]);
  // Failed status reads remove the previous link; absence means unknown, not disconnected.
  useEffect(() => {
    if (busy || hosts.length === 0) return;
    let active = true, pending = false;
    const update = () => {
      if (pending) return;
      pending = true;
      const version = scopeVersion.current;
      void Promise.all(hosts.map(async (host) => {
        try { return [host.id, (await typedIPC.getHostConnection(host.id)).connection] as const; }
        catch { return null; }
      })).then((statuses) => {
        if (active && version === scopeVersion.current) setConnections(Object.fromEntries(statuses.filter((item) => item !== null)));
      }).finally(() => { pending = false; });
    };
    update();
    const timer = window.setInterval(update, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [busy, hosts, connectionEpoch]);

  const register = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      await typedIPC.createHost({ name: name.trim(), hostname: hostname.trim(), username: username.trim(), port: Number(port), identity_file: identity.trim(), remote_port: Number(remotePort) });
      setName(""); setHostname(""); setUsername(""); setPort("22"); setIdentity(""); setRemotePort("8765");
      await refresh();
    } catch (err) { setError(String(err)); setBusy(false); }
  };
  const changeConnection = async (host: RemoteHost, connect: boolean) => {
    const version = scopeVersion.current;
    setBusy(true); setError(null);
    try {
      const result = connect
        ? await typedIPC.connectHost({ host_id: host.id })
        : await typedIPC.disconnectHost(host.id);
      if (version === scopeVersion.current) setConnections((current) => ({ ...current, [host.id]: result.connection }));
    } catch (err) { if (version === scopeVersion.current) setError(String(err)); }
    finally { setBusy(false); }
  };

  const savePort = async (host: RemoteHost) => {
    setBusy(true); setError(null);
    try {
      if (["connected", "degraded", "connecting", "reconnecting"].includes(connections[host.id]?.state ?? "")) {
        const accepted = await requestConfirmation({ title: "保存并断开现有隧道？", description: "更改远端服务端口会断开旧隧道；保存后请重新连接。", confirmLabel: "保存并断开" });
        if (!accepted) { setBusy(false); return; }
      }
      await typedIPC.updateHost(host.id, { remote_port: Number(hostPorts[host.id]) });
      await refresh();
    } catch (err) { setError(String(err)); setBusy(false); }
  };

  const beginEdit = (host: RemoteHost) => {
    setEditId(host.id); setError(null);
    setEdit({ name: host.name, hostname: host.hostname, username: host.username, port: String(host.port), identity_file: host.identity_file, remote_port: String(host.remote_port) });
  };

  const saveHost = async (event: React.FormEvent, host: RemoteHost) => {
    event.preventDefault(); setError(null);
    const port = Number(edit.port), remotePort = Number(edit.remote_port);
    if (!edit.name.trim() || !edit.hostname.trim() || !edit.username.trim() ||
        !Number.isInteger(port) || port < 1 || port > 65535 || !Number.isInteger(remotePort) || remotePort < 1 || remotePort > 65535) {
      setError("名称、主机地址和用户名不能为空，端口须为 1–65535。"); return;
    }
    const changes: Partial<import("../../types/ipc").HostConfig> = {};
    if (edit.name.trim() !== host.name) changes.name = edit.name.trim();
    if (edit.hostname.trim() !== host.hostname) changes.hostname = edit.hostname.trim();
    if (edit.username.trim() !== host.username) changes.username = edit.username.trim();
    if (port !== host.port) changes.port = port;
    if (edit.identity_file.trim() !== host.identity_file) changes.identity_file = edit.identity_file.trim();
    if (remotePort !== host.remote_port) changes.remote_port = remotePort;
    if (!Object.keys(changes).length) { setEditId(null); return; }
    setBusy(true);
    try {
      const changesConnection = Object.keys(changes).some((key) => key !== "name");
      if (changesConnection && ["connected", "degraded", "connecting", "reconnecting"].includes(connections[host.id]?.state ?? "")) {
        const accepted = await requestConfirmation({ title: "保存并断开现有隧道？", description: "更改 SSH 连接信息会断开此主机的旧隧道，远端标签页将失去连接。保存后请重新连接。", confirmLabel: "保存并断开" });
        if (!accepted) return;
      }
      const result = await typedIPC.updateHost(host.id, changes);
      setHosts((current) => current.map((item) => item.id === host.id ? result.host : item));
      setHostPorts((current) => ({ ...current, [host.id]: String(result.host.remote_port) }));
      if (changesConnection) setConnections((current) => ({ ...current, [host.id]: { host_id: host.id, state: "disconnected", local_url: null, remote_port: null, error: null } }));
      setEditId(null);
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  };

  const toggleHost = async (host: RemoteHost) => {
    setBusy(true); setError(null);
    try {
      await typedIPC.updateHost(host.id, { enabled: !host.enabled });
      await refresh();
    } catch (err) { setError(String(err)); setBusy(false); }
  };

  return <section className="space-y-4" data-testid="settings-hosts">
    <h2 className="text-sm font-semibold">远程主机</h2>
    <p className="text-xs text-ink-2">通过 SSH 隧道在独立标签页打开远端服务，当前页面保持连接当前服务。远端会话、项目和设置独立，不自动同步或迁移任务。远端需要提供更新后的前端；连接状态不代表任务持续。SSH 隧道不限制远端命令的访问权限，请仅连接可信主机。</p>
    {error && <p role="alert" className="text-xs text-status-error">{error}</p>}
    <form onSubmit={(event) => void register(event)} className="space-y-2 rounded-md border border-line p-3">
      <h3 className="text-xs font-medium">登记主机</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">名称<Input aria-label="名称" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="text-xs">主机地址<Input aria-label="主机地址" required value={hostname} onChange={(e) => setHostname(e.target.value)} /></label>
        <label className="text-xs">用户名<Input aria-label="用户名" required value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="text-xs">SSH 端口<Input aria-label="SSH 端口" type="number" min="1" max="65535" required value={port} onChange={(e) => setPort(e.target.value)} /></label>
        <label className="text-xs">远端服务端口<Input aria-label="远端服务端口" type="number" min="1" max="65535" required value={remotePort} onChange={(e) => setRemotePort(e.target.value)} /></label>
        <label className="text-xs sm:col-span-2">SSH 私钥路径（可选）<Input aria-label="SSH 私钥路径" value={identity} onChange={(e) => setIdentity(e.target.value)} /></label>
      </div>
      <Button type="submit" size="sm" disabled={busy}>登记</Button>
    </form>
    <Button size="sm" disabled={busy} onClick={() => void refresh()}>刷新状态</Button>
    {hosts.length === 0 && <p className="text-xs text-ink-2">暂无主机。</p>}
    {hosts.map((host) => {
      const connection = connections[host.id];
      const url = tunnelUrl(connection);
      return <article key={host.id} className="space-y-2 rounded-md border border-line p-3">
        <h3 className="text-xs font-medium">{host.name} ({host.username}@{host.hostname}:{host.port})</h3>
        {editId === host.id && <form className="space-y-2 rounded border border-line p-2" onSubmit={(event) => void saveHost(event, host)}>
          <p className="text-xs text-ink-2">只修改此主机的本地连接配置，不移动远端会话或文件。更改 SSH 信息会断开现有隧道。</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="text-xs">名称<Input aria-label={`${host.name} 编辑名称`} required value={edit.name} onChange={(event) => setEdit({ ...edit, name: event.target.value })} /></label>
            <label className="text-xs">主机地址<Input aria-label={`${host.name} 编辑主机地址`} required value={edit.hostname} onChange={(event) => setEdit({ ...edit, hostname: event.target.value })} /></label>
            <label className="text-xs">用户名<Input aria-label={`${host.name} 编辑用户名`} required value={edit.username} onChange={(event) => setEdit({ ...edit, username: event.target.value })} /></label>
            <label className="text-xs">SSH 端口<Input aria-label={`${host.name} 编辑 SSH 端口`} type="number" min="1" max="65535" required value={edit.port} onChange={(event) => setEdit({ ...edit, port: event.target.value })} /></label>
            <label className="text-xs">远端服务端口<Input aria-label={`${host.name} 编辑远端服务端口`} type="number" min="1" max="65535" required value={edit.remote_port} onChange={(event) => setEdit({ ...edit, remote_port: event.target.value })} /></label>
            <label className="text-xs">SSH 私钥路径（留空使用默认配置）<Input aria-label={`${host.name} 编辑 SSH 私钥路径`} value={edit.identity_file} onChange={(event) => setEdit({ ...edit, identity_file: event.target.value })} /></label>
          </div>
          <div className="flex gap-2"><Button type="submit" size="sm" disabled={busy}>保存连接配置</Button><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => setEditId(null)}>取消</Button></div>
        </form>}
        <div className="flex items-end gap-2">
          <label className="text-xs">远端服务端口<Input aria-label={`${host.name} 远端服务端口`} type="number" min="1" max="65535" value={hostPorts[host.id] ?? ""} onChange={(e) => setHostPorts((current) => ({ ...current, [host.id]: e.target.value }))} /></label>
          <Button size="sm" disabled={busy || !Number.isInteger(Number(hostPorts[host.id])) || Number(hostPorts[host.id]) < 1 || Number(hostPorts[host.id]) > 65535 || Number(hostPorts[host.id]) === host.remote_port} onClick={() => void savePort(host)}>保存端口</Button>
        </div>
        <p className="text-xs text-ink-2">{!host.enabled ? "已禁用 · " : ""}状态：{connection ? STATUS_LABELS[connection.state] : "状态待核对"}</p>
        {connection?.state === "degraded" && <p className="text-xs text-ink-2">SSH 进程仍在，但远端服务健康检查未通过；会继续检查。请检查远端服务，必要时断开后重新连接。此状态不判断远端任务是否运行。</p>}
        {!connection && <p className="text-xs text-ink-2">无法确认当前隧道状态；请恢复本地服务连接或刷新状态。</p>}
        <HostFileTransfer host={host} />
        {connection?.error && <p role="alert" className="text-xs text-status-error">{connection.error}</p>}
        {connection?.state === "connected" && !url && <p role="alert" className="text-xs text-status-error">隧道地址无效，无法打开。</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={busy || editId !== null} onClick={() => beginEdit(host)}>编辑连接配置</Button>
          <Button size="sm" disabled={busy || !host.enabled || !connection || ["connected", "degraded", "connecting", "reconnecting"].includes(connection.state)} onClick={() => void changeConnection(host, true)}>连接</Button>
          <Button size="sm" disabled={busy || !connection || !["connected", "degraded", "connecting", "reconnecting"].includes(connection.state)} onClick={() => void changeConnection(host, false)}>断开</Button>
          <Button size="sm" disabled={busy} onClick={() => void toggleHost(host)}>{host.enabled ? "禁用" : "启用"}</Button>
          {url && <a className="text-xs text-accent underline" href={tunnelPageUrl(url)} target="_blank" rel="noopener noreferrer">打开远端独立标签页</a>}
        </div>
      </article>;
    })}
  </section>;
}
