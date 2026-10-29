import { useCallback, useEffect, useState } from "react";
import { typedIPC } from "../../ipc";
import type { HostConnection, RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";

const STATUS_LABELS: Record<HostConnection["state"], string> = {
  connected: "已连接", disconnected: "已断开", connecting: "连接中",
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
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [username, setUsername] = useState("");
  const [port, setPort] = useState("22");
  const [identity, setIdentity] = useState("");
  const [remotePort, setRemotePort] = useState("8765");

  const refresh = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const result = await typedIPC.listHosts();
      setHosts(result.hosts);
      const statuses = await Promise.all(result.hosts.map(async (host) => {
        try { return [host.id, (await typedIPC.getHostConnection(host.id)).connection] as const; }
        catch (err) { return [host.id, { host_id: host.id, state: "failed", local_url: null, remote_port: null, error: String(err) } as HostConnection] as const; }
      }));
      setConnections(Object.fromEntries(statuses));
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  // Refresh process state while this panel is visible. Stop polling while a
  // user operation is in flight so an older response cannot undo its result.
  useEffect(() => {
    if (busy || hosts.length === 0) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void Promise.all(hosts.map(async (host) => {
        try { return [host.id, (await typedIPC.getHostConnection(host.id)).connection] as const; }
        catch { return null; }
      })).then((statuses) => {
        if (!active) return;
        setConnections((current) => {
          const next = { ...current };
          for (const status of statuses) if (status) next[status[0]] = status[1];
          return next;
        });
      }).finally(() => { pending = false; });
    }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [busy, hosts]);

  const register = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      await typedIPC.createHost({ name: name.trim(), hostname: hostname.trim(), username: username.trim(), port: Number(port), identity_file: identity.trim() });
      setName(""); setHostname(""); setUsername(""); setPort("22"); setIdentity("");
      await refresh();
    } catch (err) { setError(String(err)); setBusy(false); }
  };
  const changeConnection = async (host: RemoteHost, connect: boolean) => {
    setBusy(true); setError(null);
    try {
      const result = connect
        ? await typedIPC.connectHost({ host_id: host.id, remote_port: Number(remotePort) })
        : await typedIPC.disconnectHost(host.id);
      setConnections((current) => ({ ...current, [host.id]: result.connection }));
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
    <p className="text-xs text-ink-2">通过 SSH 隧道在新标签页打开远端服务。远端拥有独立的会话、文件和设置；当前页面仍连接本机。SSH 隧道不限制远端命令的访问权限，执行环境由远端配置决定。请仅连接可信主机，并确认远端服务正在运行。</p>
    {error && <p role="alert" className="text-xs text-status-error">{error}</p>}
    <form onSubmit={(event) => void register(event)} className="space-y-2 rounded-md border border-line p-3">
      <h3 className="text-xs font-medium">登记主机</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">名称<Input aria-label="名称" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="text-xs">主机地址<Input aria-label="主机地址" required value={hostname} onChange={(e) => setHostname(e.target.value)} /></label>
        <label className="text-xs">用户名<Input aria-label="用户名" required value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="text-xs">SSH 端口<Input aria-label="SSH 端口" type="number" min="1" max="65535" required value={port} onChange={(e) => setPort(e.target.value)} /></label>
        <label className="text-xs sm:col-span-2">SSH 私钥路径（可选）<Input aria-label="SSH 私钥路径" value={identity} onChange={(e) => setIdentity(e.target.value)} /></label>
      </div>
      <Button type="submit" size="sm" disabled={busy}>登记</Button>
    </form>
    <div className="flex items-end gap-2">
      <label className="text-xs">远端服务端口<Input aria-label="远端服务端口" type="number" min="1" max="65535" value={remotePort} onChange={(e) => setRemotePort(e.target.value)} /></label>
      <Button size="sm" disabled={busy} onClick={() => void refresh()}>刷新状态</Button>
    </div>
    {hosts.length === 0 && <p className="text-xs text-ink-2">暂无主机。</p>}
    {hosts.map((host) => {
      const connection = connections[host.id];
      const url = tunnelUrl(connection);
      return <article key={host.id} className="space-y-2 rounded-md border border-line p-3">
        <h3 className="text-xs font-medium">{host.name} ({host.username}@{host.hostname}:{host.port})</h3>
        <p className="text-xs text-ink-2">{!host.enabled ? "已禁用 · " : ""}状态：{connection ? STATUS_LABELS[connection.state] : "未知"}</p>
        {connection?.error && <p role="alert" className="text-xs text-status-error">{connection.error}</p>}
        {connection?.state === "connected" && !url && <p role="alert" className="text-xs text-status-error">隧道地址无效，无法打开。</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy || !host.enabled || connection?.state === "connected" || connection?.state === "reconnecting" || !Number.isInteger(Number(remotePort)) || Number(remotePort) < 1 || Number(remotePort) > 65535} onClick={() => void changeConnection(host, true)}>连接</Button>
          <Button size="sm" disabled={busy || (connection?.state !== "connected" && connection?.state !== "reconnecting")} onClick={() => void changeConnection(host, false)}>断开</Button>
          <Button size="sm" disabled={busy} onClick={() => void toggleHost(host)}>{host.enabled ? "禁用" : "启用"}</Button>
          {url && <a className="text-xs text-accent underline" href={url} target="_blank" rel="noopener noreferrer">在新标签页打开远端</a>}
        </div>
      </article>;
    })}
  </section>;
}
