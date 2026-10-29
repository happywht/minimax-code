import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";

type Entry = { name: string; kind: "directory" | "file" };
type Listing = { path: string; entries: Entry[]; truncated: boolean };

// Both the program and the path are encoded as data; neither enters shell syntax.
const PROGRAM = `import base64,itertools,json,os,sys
p=os.fsdecode(base64.b64decode(sys.argv[1],validate=True))
if not p.startswith('/') or '\\x00' in p: raise ValueError('absolute path required')
p=os.path.normpath(p)
with os.scandir(p) as scan:
    items=list(itertools.islice(scan,201))
entries=[]
for item in items[:200]:
    try:
        if item.is_dir(follow_symlinks=False): kind='directory'
        elif item.is_file(follow_symlinks=False): kind='file'
        else: continue
        entries.append({'name':item.name,'kind':kind})
    except OSError: continue
entries.sort(key=lambda e:(e['kind']!='directory',e['name']))
truncated=len(items)>200
while True:
    output=json.dumps({'path':p,'entries':entries,'truncated':truncated},ensure_ascii=True)
    if len(output)<=18000: break
    if not entries: raise ValueError('directory path too long to return')
    entries.pop()
    truncated=True
print(output)`;
const encode = (text: string) => btoa(Array.from(new TextEncoder().encode(text), (b) => String.fromCharCode(b)).join(""));
const command = (path: string) => `python3 -c "import base64;exec(base64.b64decode('${encode(PROGRAM)}'))" '${encode(path)}'`;

export function HostFileBrowser({ host, onSelect }: { host: RemoteHost; onSelect: (path: string) => void }): JSX.Element {
  const [path, setPath] = useState("/"), [listing, setListing] = useState<Listing | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), base = ipc.agentBaseUrl;
  const identity = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.enabled]);
  useEffect(() => {
    epoch.current++; setPath("/"); setListing(null); setBusy(false); setError("");
    return () => { epoch.current++; };
  }, [identity, base]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setListing(null); setBusy(false); setError(""); }), []);

  const browse = async (directory: string) => {
    if (!host.enabled || busy) return;
    if (!directory.startsWith("/") || directory.includes("\0") || directory.length > 4096) {
      setError("请输入不超过 4096 字符的 Linux 绝对目录路径。"); return;
    }
    const ticket = ++epoch.current; setBusy(true); setError(""); setListing(null);
    try {
      const result = await typedIPC.execHost({ host_id: host.id, command: command(directory), timeout_s: 15 });
      if (ticket !== epoch.current || ipc.agentBaseUrl !== base) return;
      if (!result.ok) throw new Error(result.timed_out ? "读取超时" : result.stderr || "目录读取失败");
      const data: unknown = JSON.parse(result.stdout);
      if (!data || typeof data !== "object") throw new Error("目录响应无效");
      const row = data as Record<string, unknown>;
      if (typeof row.path !== "string" || !row.path.startsWith("/") || !Array.isArray(row.entries) || row.entries.length > 200 || typeof row.truncated !== "boolean" ||
          !row.entries.every((entry: unknown) => !!entry && typeof entry === "object" && typeof (entry as Entry).name === "string" && (entry as Entry).name !== "." && (entry as Entry).name !== ".." && !(entry as Entry).name.includes("/") && ((entry as Entry).kind === "file" || (entry as Entry).kind === "directory"))) throw new Error("目录响应无效");
      setPath(row.path); setListing({ path: row.path, entries: row.entries as Entry[], truncated: row.truncated });
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
    {listing && <div className="text-xs"><Button type="button" size="sm" variant="secondary" disabled={busy || listing.path === "/"} onClick={() => void browse(parent)}>上一级</Button>
      {listing.truncated && <p>结果达到数量或长度上限，列表不完整；请直接输入更具体的目录。</p>}
      <ul className="max-h-48 overflow-auto">{listing.entries.map((entry) => <li key={entry.name} className="flex items-center gap-2 break-all">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => entry.kind === "directory" ? void browse(child(entry.name)) : onSelect(child(entry.name))}>{entry.kind === "directory" ? "打开" : "选择"}</Button>
        <span>{entry.kind === "directory" ? "目录" : "文件"}：{entry.name}</span></li>)}</ul>
      {!listing.entries.length && <p>没有可显示的普通文件或目录。</p>}
    </div>}
    <p className="text-xs text-ink-2">通过独立 SSH 读取，不显示符号链接或特殊文件；选择文件只填入路径，不会传输。目录内容可能变化，请在确认传输前核对。</p>
  </div>;
}
