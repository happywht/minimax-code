import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

const localAbsolute = (value: string) => /^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)|\/)/.test(value);
function safeName(value: string): boolean {
  return !!value && value !== "." && value !== ".." && !value.includes("/") && !value.includes("\\") && !/[<>:"|?*]|\p{Cc}|\p{Cf}|\p{Cs}/u.test(value) &&
    !/[. ]$/.test(value) && !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(value);
}
type Pending = { service: string; host: string; remote: string; target: string };
function destination(directory: string, filename: string): string {
  const separator = /^(?:[A-Za-z]:[\\/]|\\\\)/.test(directory) ? "\\" : "/";
  return directory.replace(/[\\/]+$/, "") + separator + filename;
}

/** Explicit, no-overwrite download to the machine running the current service. */
export function HostBoundDownload({ host, remotePath, active }: { host: RemoteHost; remotePath: string; active: boolean }): JSX.Element {
  const [directory, setDirectory] = useState(""), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [receipt, setReceipt] = useState<{ path: string; sha256: string; size: number } | null>(null);
  const inFlight = useRef(false), dispatched = useRef(false), epoch = useRef(0), base = ipc.agentBaseUrl;
  const storageKey = `minimax:bound-download:${JSON.stringify([base, host.id])}`;
  const readPending = (): Pending | null | "unavailable" => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw) return null;
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) return "unavailable";
      const record = value as Record<string, unknown>;
      if (!["service", "host", "remote", "target"].every((key) => typeof record[key] === "string" && (record[key] as string).length > 0 && (record[key] as string).length <= 4096)) return "unavailable";
      return { service: String(record.service), host: String(record.host), remote: String(record.remote), target: String(record.target) };
    } catch { return "unavailable"; }
  };
  const [pending, setPending] = useState<Pending | null | "unavailable">(() => readPending());
  const scope = JSON.stringify([base, host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled, remotePath, active]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const hostScope = JSON.stringify([base, host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
  useEffect(() => { setDirectory(""); }, [hostScope]);
  useEffect(() => {
    epoch.current++; setReceipt(null); setPending(readPending());
    setMessage(dispatched.current ? "连接或选择已变化；保存结果可能未知，请检查原目标文件。" : "");
    return () => { epoch.current++; };
  }, [scope]);
  useEffect(() => ipc.onSideCar(() => {
    epoch.current++; setReceipt(null); setDirectory("");
    setMessage(dispatched.current ? "连接已变化；保存结果可能未知，请检查原目标文件。" : "连接已变化，请重新选择远端文件。");
  }), []);
  const name = remotePath.split("/").pop() || "";
  const target = localAbsolute(directory.trim()) && safeName(name) ? destination(directory.trim(), name) : "";
  const save = async () => {
    if (inFlight.current || pending || !active || !host.enabled || !target || !remotePath.startsWith("/")) return;
    inFlight.current = true; setBusy(true); setMessage(""); setReceipt(null);
    const ticket = epoch.current, snapshot = scope, origin = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && snapshot === scopeRef.current && origin === ipc.agentBaseUrl;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const accepted = await requestConfirmation({ title: "下载远端文件到服务机器？", description: `从 ${host.username}@${host.hostname}:${host.port} 读取 ${remotePath} 并保存到 ${target}。只创建新文件，已有目标不会覆盖；最大 10 MiB。`, confirmLabel: "确认下载" });
      if (!accepted || !current()) return;
      if (readPending()) throw new Error("A pending download needs review");
      const record: Pending = { service: identity.service_id, host: `${host.username}@${host.hostname}:${host.port}`, remote: remotePath, target };
      sessionStorage.setItem(storageKey, JSON.stringify(record)); setPending(record);
      dispatched.current = true; setMessage("下载中；请勿重复提交。若连接中断，请检查目标文件。");
      const result = await typedIPC.fetchBoundHostFile({ host_id: host.id, expected_service_id: identity.service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, remote_path: remotePath, local_path: target });
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== identity.service_id) throw new Error("服务身份已变化");
      if (!result.ok) {
        if (result.ok !== false || result.reason !== "exists" || result.sha256 !== null || result.size_bytes !== null) throw new Error("Invalid result");
        if (JSON.stringify(readPending()) !== JSON.stringify(record)) throw new Error("Pending record changed");
        sessionStorage.removeItem(storageKey); setPending(null);
        setMessage("目标已存在，未覆盖；请选择其他目录。"); return;
      }
      if (!/^[0-9a-f]{64}$/.test(result.sha256) || !Number.isSafeInteger(result.size_bytes) || result.size_bytes < 0 || result.size_bytes > 10485760) throw new Error("下载回执无效");
      if (JSON.stringify(readPending()) !== JSON.stringify(record)) throw new Error("Pending record changed");
      sessionStorage.removeItem(storageKey); setPending(null);
      setReceipt({ path: target, sha256: result.sha256, size: result.size_bytes });
      setMessage("文件已校验并创建；请核对内容，校验不代表业务正确。");
    } catch { if (current()) { setPending(readPending()); setMessage(dispatched.current ? "保存结果未确认；请先检查目标文件，再决定是否重试。" : "未发起下载；请核对连接及浏览器存储。"); } }
    finally { dispatched.current = false; inFlight.current = false; setBusy(false); }
  };
  const clearPending = async () => {
    if (!pending || inFlight.current) return;
    const expected = pending, ticket = epoch.current, snapshot = scope;
    const accepted = await requestConfirmation({ title: "清除待核对的下载记录？", description: "先到原服务机器核对目标文件。清除仅删除本标签页记录，不删除或撤销可能已创建的文件。", confirmLabel: "已核对，清除记录" });
    if (!accepted || ticket !== epoch.current || snapshot !== scopeRef.current || JSON.stringify(readPending()) !== JSON.stringify(expected)) return;
    try { sessionStorage.removeItem(storageKey); setPending(null); setMessage("本地待核对记录已清除，未修改目标文件。"); }
    catch { setMessage("无法清除待核对记录，请检查浏览器存储。"); }
  };
  return <div className="space-y-1 rounded border border-line p-2 text-xs">
    <p>保存已选远端文件到运行当前服务的机器（可能不是浏览器所在机器）。</p>
    <p className="break-all">远端文件：{remotePath || "请在上方目录列表选择文件"}</p>
    <label className="block">已存在的本机目标目录<Input aria-label={`${host.name} 下载目标目录`} value={directory} disabled={busy} onChange={(event) => { setDirectory(event.target.value); setReceipt(null); setMessage(""); }} placeholder="C:\\Downloads" /></label>
    {remotePath && !safeName(name) && <p role="alert">文件名不能安全映射到本机；请使用其他明确的下载路径。</p>}
    {target && <p className="break-all">新文件路径：{target}</p>}
    <Button type="button" size="sm" variant="secondary" disabled={busy || !!pending || !active || !host.enabled || !target || !remotePath.startsWith("/")} onClick={() => void save()}>保存所选文件到新路径</Button>
    {pending && <div className="space-y-1" role="status"><p>有下载结果待核对；请检查原目标文件，确认前不能再发起此主机的绑定下载。</p>
      {pending !== "unavailable" ? <p className="break-all">服务：{pending.service} · 主机：{pending.host} · 远端：{pending.remote} · 目标：{pending.target}</p> : <p>记录损坏或浏览器存储不可用；请核对历史后处理。</p>}
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void clearPending()}>已核对，清除记录</Button></div>}
    {message && <p role="status" className="break-words">{message}</p>}
    {receipt && <p className="break-all">{receipt.path} · {receipt.size} 字节 · SHA-256 {receipt.sha256}</p>}
  </div>;
}
