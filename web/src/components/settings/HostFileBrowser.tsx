import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";

type Entry = { name: string; kind: "directory" | "file" };
type Listing = { path: string; entries: Entry[]; offset: number; nextOffset: number | null; limited: boolean };

export function HostFileBrowser({ host, onSelect, onSelectDirectory }: { host: RemoteHost; onSelect: (path: string) => void; onSelectDirectory: (path: string) => void }): JSX.Element {
  const [path, setPath] = useState("/"), [listing, setListing] = useState<Listing | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), base = ipc.agentBaseUrl;
  const identity = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  useEffect(() => {
    epoch.current++; setPath("/"); setListing(null); setHistory([]); setBusy(false); setError("");
    return () => { epoch.current++; };
  }, [identity, base]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setListing(null); setHistory([]); setBusy(false); setError(""); }), []);

  const browse = async (directory: string, offset = 0, previous: number[] = []) => {
    if (!host.enabled || busy) return;
    if (!directory.startsWith("/") || directory.includes("\0") || directory.length > 4096) {
      setError("请输入不超过 4096 字符的 Linux 绝对目录路径。"); return;
    }
    const ticket = ++epoch.current; setBusy(true); setError(""); setListing(null);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== base) return;
      const row = await typedIPC.listHostDirectory({ host_id: host.id, expected_service_id: identity.service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, remote_path: directory, offset });
      if (ticket !== epoch.current || ipc.agentBaseUrl !== base) return;
      const after = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== base) return;
      if (after.service_id !== identity.service_id || row.path !== directory || row.offset !== offset ||
          !Array.isArray(row.entries) || row.entries.length > 200 || typeof row.limited !== "boolean" ||
          !(row.next_offset === null || (Number.isInteger(row.next_offset) && row.next_offset > offset && row.next_offset <= 10000)) ||
          !row.entries.every((entry) => !!entry && typeof entry.name === "string" && entry.name !== "." && entry.name !== ".." && !entry.name.includes("/") && (entry.kind === "file" || entry.kind === "directory"))) throw new Error("服务身份或目录响应不匹配");
      setPath(row.path); setHistory(previous); setListing({ path: row.path, entries: row.entries as Entry[], offset, nextOffset: row.next_offset as number | null, limited: row.limited as boolean });
    } catch (err) { if (ticket === epoch.current && ipc.agentBaseUrl === base) setError(err instanceof Error ? err.message : String(err)); }
    finally { if (ticket === epoch.current && ipc.agentBaseUrl === base) setBusy(false); }
  };
  const child = (name: string) => `${listing?.path === "/" ? "" : listing?.path}/${name}`;
  const parent = (listing?.path || "/").replace(/\/[^/]+$/, "") || "/";
  return <div className="space-y-1 rounded border border-line p-2">
    <p className="text-xs">浏览远端目录（只读，需远端 Python 3）</p>
    <div className="flex gap-2"><Input aria-label={`${host.name} 远端浏览目录`} value={path} disabled={busy} onChange={(e) => setPath(e.target.value)} />
      <Button type="button" size="sm" disabled={busy || !host.enabled} onClick={() => void browse(path)}>读取目录</Button></div>
    {busy && <p role="status" className="text-xs">正在读取目录…</p>}
    {error && <p role="alert" className="break-words text-xs text-status-error">{error}</p>}
    {listing && <div className="text-xs"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => onSelectDirectory(listing.path)}>选择当前目录作为上传位置</Button><Button type="button" size="sm" variant="secondary" disabled={busy || listing.path === "/"} onClick={() => void browse(parent)}>上一级</Button>
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || !history.length} onClick={() => void browse(listing.path, history[history.length - 1], history.slice(0, -1))}>上一页</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || listing.nextOffset === null} onClick={() => { if (listing.nextOffset !== null) void browse(listing.path, listing.nextOffset, [...history, listing.offset]); }}>下一页</Button></div>
      {listing.limited && <p>已达到扫描上限；请直接输入更具体的目录或文件路径。</p>}
      <ul className="max-h-48 overflow-auto">{listing.entries.map((entry) => <li key={entry.name} className="flex items-center gap-2 break-all">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => entry.kind === "directory" ? void browse(child(entry.name)) : onSelect(child(entry.name))}>{entry.kind === "directory" ? "打开" : "选择"}</Button>
        <span>{entry.kind === "directory" ? "目录" : "文件"}：{entry.name}</span></li>)}</ul>
      {!listing.entries.length && <p>没有可显示的普通文件或目录。</p>}
    </div>}
    <p className="text-xs text-ink-2">通过受信 SSH 浏览；不跟随符号链接，不显示特殊文件。选择不会传输文件；</p><p className="text-xs text-ink-2">目录变化可能导致翻页重复或遗漏，请在传输前核对。</p>
  </div>;
}
