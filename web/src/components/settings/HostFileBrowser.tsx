import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";

type Entry = { name: string; kind: "directory" | "file" };
type Listing = { path: string; entries: Entry[]; offset: number; nextOffset: number | null; limited: boolean };

// Both the program and the path are encoded as data; neither enters shell syntax.
const PROGRAM = `import base64,itertools,json,os,sys
p=os.fsdecode(base64.b64decode(sys.argv[1],validate=True))
if not p.startswith('/') or '\\x00' in p: raise ValueError('absolute path required')
p=os.path.normpath(p)
offset=int(sys.argv[2])
if offset < 0 or offset > 10000: raise ValueError('invalid offset')
with os.scandir(p) as scan:
    items=list(itertools.islice(scan,offset,offset+201))
entries=[]
consumed=0
for item in items[:200]:
    try:
        if item.is_dir(follow_symlinks=False): kind='directory'
        elif item.is_file(follow_symlinks=False): kind='file'
        else:
            consumed+=1
            continue
        entry={'name':item.name,'kind':kind}
        candidate=json.dumps({'path':p,'entries':entries+[entry],'offset':offset,'next_offset':10000,'limited':True},ensure_ascii=True)
        if len(candidate)>18000:
            if not consumed: raise ValueError('directory entry too long to return')
            break
        entries.append(entry)
    except OSError: pass
    consumed+=1
more=consumed<len(items)
next_offset=offset+consumed if more and offset+consumed<=10000 else None
limited=more and next_offset is None
print(json.dumps({'path':p,'entries':entries,'offset':offset,'next_offset':next_offset,'limited':limited},ensure_ascii=True))`;
const encode = (text: string) => btoa(Array.from(new TextEncoder().encode(text), (b) => String.fromCharCode(b)).join(""));
const command = (path: string, offset: number) => `python3 -c "import base64;exec(base64.b64decode('${encode(PROGRAM)}'))" '${encode(path)}' ${offset}`;

export function HostFileBrowser({ host, onSelect }: { host: RemoteHost; onSelect: (path: string) => void }): JSX.Element {
  const [path, setPath] = useState("/"), [listing, setListing] = useState<Listing | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), base = ipc.agentBaseUrl;
  const identity = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.enabled]);
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
      const result = await typedIPC.execHost({ host_id: host.id, command: command(directory, offset), timeout_s: 15 });
      if (ticket !== epoch.current || ipc.agentBaseUrl !== base) return;
      if (!result.ok) throw new Error(result.timed_out ? "读取超时" : result.stderr || "目录读取失败");
      const data: unknown = JSON.parse(result.stdout);
      if (!data || typeof data !== "object") throw new Error("目录响应无效");
      const row = data as Record<string, unknown>;
      if (typeof row.path !== "string" || !row.path.startsWith("/") || !Array.isArray(row.entries) || row.entries.length > 200 || row.offset !== offset || typeof row.limited !== "boolean" ||
          !(row.next_offset === null || (typeof row.next_offset === "number" && Number.isInteger(row.next_offset) && row.next_offset > offset && row.next_offset <= 10000)) ||
          !row.entries.every((entry: unknown) => !!entry && typeof entry === "object" && typeof (entry as Entry).name === "string" && (entry as Entry).name !== "." && (entry as Entry).name !== ".." && !(entry as Entry).name.includes("/") && ((entry as Entry).kind === "file" || (entry as Entry).kind === "directory"))) throw new Error("目录响应无效");
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
    {listing && <div className="text-xs"><Button type="button" size="sm" variant="secondary" disabled={busy || listing.path === "/"} onClick={() => void browse(parent)}>上一级</Button>
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || !history.length} onClick={() => void browse(listing.path, history[history.length - 1], history.slice(0, -1))}>上一页</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || listing.nextOffset === null} onClick={() => { if (listing.nextOffset !== null) void browse(listing.path, listing.nextOffset, [...history, listing.offset]); }}>下一页</Button></div>
      {listing.limited && <p>已达到扫描上限；请直接输入更具体的目录或文件路径。</p>}
      <ul className="max-h-48 overflow-auto">{listing.entries.map((entry) => <li key={entry.name} className="flex items-center gap-2 break-all">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => entry.kind === "directory" ? void browse(child(entry.name)) : onSelect(child(entry.name))}>{entry.kind === "directory" ? "打开" : "选择"}</Button>
        <span>{entry.kind === "directory" ? "目录" : "文件"}：{entry.name}</span></li>)}</ul>
      {!listing.entries.length && <p>没有可显示的普通文件或目录。</p>}
    </div>}
    <p className="text-xs text-ink-2">通过独立 SSH 读取，不显示符号链接或特殊文件；选择文件只填入路径，不会传输。按目录原有顺序分批读取；目录变化可能导致翻页重复或遗漏，请在确认传输前核对。</p>
  </div>;
}
