import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostTransferResult, RemoteHost } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { HostFileBrowser } from "./HostFileBrowser";

type UploadReceipt = { hostSignature: string; remotePath: string; transferId: string };
const signature = (host: RemoteHost) => JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file]);
const isLocalAbsolute = (path: string) => /^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(path);
const isRemoteAbsolute = (path: string) => path.startsWith("/") && path !== "/" && !path.endsWith("/") && !path.includes("//") && !path.split("/").some((part) => part === ".." || part === ".");

export function HostFileTransfer({ host }: { host: RemoteHost }): JSX.Element {
  const [local, setLocal] = useState(""), [remote, setRemote] = useState("");
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null), [status, setStatus] = useState<HostTransferResult | null>(null);
  const [manualId, setManualId] = useState(""), [manualRemote, setManualRemote] = useState("");
  const [recoveryInput, setRecoveryInput] = useState("");
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0), base = ipc.agentBaseUrl, hostSignature = signature(host);
  const storageKey = `minimax:host-transfer:${base}:${host.id}`;
  useEffect(() => {
    epoch.current++; setBusy(false); setMessage(""); setStatus(null); setReceipt(null); setManualId(""); setManualRemote(""); setRecoveryInput("");
    try { const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null") as UploadReceipt | null;
      if (saved?.hostSignature === hostSignature && /^[0-9a-f]{32}$/.test(saved.transferId) && isRemoteAbsolute(saved.remotePath)) {
        setReceipt(saved); setManualId(saved.transferId); setManualRemote(saved.remotePath);
      }
    } catch { /* Storage can be unavailable; no saved receipt is claimed. */ }
    return () => { epoch.current++; };
  }, [storageKey, hostSignature]);
  const valid = (ticket: number) => epoch.current === ticket && ipc.agentBaseUrl === base;
  const recoveryRecord = receipt ? JSON.stringify({
    version: 1, service: base, host_id: host.id, hostname: host.hostname,
    username: host.username, port: host.port, remote_path: receipt.remotePath, transfer_id: receipt.transferId,
  }, null, 2) : "";
  const copyRecovery = async () => {
    try { await navigator.clipboard.writeText(recoveryRecord); setMessage("恢复记录已复制，请自行保存。"); }
    catch { setMessage("无法访问剪贴板，请手动选择并复制下方恢复记录。"); }
  };
  const applyRecovery = () => {
    try {
      if (recoveryInput.length > 8192) throw new Error("恢复记录过长");
      const saved: unknown = JSON.parse(recoveryInput);
      if (!saved || typeof saved !== "object" || Array.isArray(saved)) throw new Error("恢复记录格式无效");
      const record = saved as Record<string, unknown>;
      if (record.version !== 1 || record.service !== base || record.host_id !== host.id ||
          record.hostname !== host.hostname || record.username !== host.username || record.port !== host.port) {
        throw new Error("记录与当前服务或主机连接信息不匹配");
      }
      if (typeof record.remote_path !== "string" || !isRemoteAbsolute(record.remote_path) ||
          typeof record.transfer_id !== "string" || !/^[0-9a-f]{32}$/.test(record.transfer_id)) {
        throw new Error("记录中的远端路径或传输编号无效");
      }
      setManualRemote(record.remote_path); setManualId(record.transfer_id); setStatus(null);
      setMessage("已填入查询信息；请核对后点击查询上传状态。未发起远程操作。");
    } catch (e) { setMessage(e instanceof Error ? e.message : "恢复记录格式无效"); }
  };
  const confirm = async (action: "upload" | "download") => requestConfirmation({
    title: action === "upload" ? "确认上传到远端？" : "确认下载到本机？",
    description: action === "upload" ? `主机 ${host.name} (${host.username}@${host.hostname})：将本机 ${local} 发送到 ${remote}。若目标存在，将在校验和未变化确认后替换，并保留远端备份。` : `主机 ${host.name} (${host.username}@${host.hostname})：将 ${remote} 下载到本机 ${local}。目标已存在时不会覆盖，请选择新路径。`,
    confirmLabel: action === "upload" ? "确认上传" : "确认下载",
  });
  const transfer = async (action: "upload" | "download") => {
    if (!host.enabled || busy) return;
    if (action === "upload" && receipt && !(status?.transfer_id === receipt.transferId && ["published", "aborted", "not_published"].includes(status.publication_state ?? ""))) {
      setMessage("上一笔上传尚未确认终态；请先查询其状态并记录编号，避免覆盖待查记录。"); return;
    }
    if (!isLocalAbsolute(local.trim()) || !isRemoteAbsolute(remote.trim())) { setMessage("请输入本机绝对文件路径及远端 Linux 绝对文件路径，远端路径不能包含 ..。"); return; }
    const ticket = ++epoch.current; setBusy(true); setMessage(""); setStatus(null);
    try {
      if (!await confirm(action) || !valid(ticket)) return;
      if (action === "upload") {
        const id = crypto.randomUUID().replace(/-/g, "");
        const next = { hostSignature, remotePath: remote.trim(), transferId: id };
        try { sessionStorage.setItem(storageKey, JSON.stringify(next)); }
        catch { throw new Error("无法保存传输编号，未开始上传；请允许本标签页的会话存储。"); }
        setReceipt(next); setManualId(id); setManualRemote(next.remotePath);
        const result = await typedIPC.pushHostFile({ host_id: host.id, local_path: local.trim(), remote_path: next.remotePath, transfer_id: id });
        if (!valid(ticket)) return;
        if (result.transfer_id !== id || result.remote_path !== next.remotePath) throw new Error("上传回执与请求不匹配；请用传输编号查询状态。");
        setStatus(result); setMessage(result.ok && result.publication_state === "published" ? "上传已确认发布并校验。" : "上传未确认完成；请先查询状态，避免重复上传。");
      } else {
        const result = await typedIPC.fetchHostFile({ host_id: host.id, local_path: local.trim(), remote_path: remote.trim() });
        if (!valid(ticket)) return;
        setMessage(result.ok ? `下载已校验并写入 ${result.local_path}。` : `下载未完成：${result.stderr || "远端传输或校验失败"}`);
      }
    } catch (e) { if (valid(ticket)) setMessage(`${e instanceof Error ? e.message : String(e)}；结果可能不明，请先核对目标${action === "upload" ? "或查询传输状态" : "文件"}。`); }
    finally { if (valid(ticket)) setBusy(false); }
  };
  const inspect = async () => {
    if (!host.enabled || !isRemoteAbsolute(manualRemote.trim()) || !/^[0-9a-f]{32}$/.test(manualId)) { setMessage("请输入远端绝对路径和 32 位小写十六进制传输编号。"); return; }
    const ticket = ++epoch.current; setBusy(true); setMessage(""); setStatus(null);
    try { const result = await typedIPC.getHostTransferStatus({ host_id: host.id, remote_path: manualRemote.trim(), transfer_id: manualId });
      if (!valid(ticket)) return;
      if (result.transfer_id !== manualId || result.remote_path !== manualRemote.trim()) throw new Error("状态回执与请求不匹配");
      setStatus(result); setMessage(result.ok ? "已读取远端上传记录；状态不代表其他文件操作。" : "状态未确认；请检查连接或远端记录，不要自动重试上传。");
    } catch (e) { if (valid(ticket)) setMessage(`${e instanceof Error ? e.message : String(e)}；上传结果仍未知。`); }
    finally { if (valid(ticket)) setBusy(false); }
  };
  return <details className="rounded border border-line p-2"><summary>上传 / 下载文件</summary>
    <p className="text-xs text-ink-2">本机路径指运行当前服务的机器，不是浏览器选中的文件；远端路径指此主机的 Linux 绝对路径。仅单文件、显式操作，不自动同步。传输使用独立 SSH/SFTP，服务隧道状态不代表传输可用。</p>
    <label className="block text-xs">本机文件绝对路径<Input aria-label={`${host.name} 本机传输路径`} value={local} disabled={busy} onChange={(e) => setLocal(e.target.value)} placeholder="C:\\资料\\报告.pdf" /></label>
    <label className="block text-xs">远端文件绝对路径<Input aria-label={`${host.name} 远端传输路径`} value={remote} disabled={busy} onChange={(e) => setRemote(e.target.value)} placeholder="/home/user/report.pdf" /></label>
    <HostFileBrowser host={host} onSelect={(path) => { if (!busy) setRemote(path); }} />
    <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy || !host.enabled} onClick={() => void transfer("upload")}>上传到此主机</Button>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void transfer("download")}>从此主机下载</Button></div>
    <p className="text-xs text-ink-2">上传备份位于远端传输记录目录；下载只创建新文件，不覆盖本机已有文件。两者进行 SHA-256 比对，不能证明业务内容正确。响应中断时先核对，不自动重试。</p>
    <label className="block text-xs">上传传输编号<Input aria-label={`${host.name} 上传传输编号`} value={manualId} maxLength={32} disabled={busy} onChange={(e) => setManualId(e.target.value)} /></label>
    <label className="block text-xs">查询用远端路径<Input aria-label={`${host.name} 上传查询路径`} value={manualRemote} disabled={busy} onChange={(e) => setManualRemote(e.target.value)} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void inspect()}>查询上传状态</Button>
    {receipt && <p className="break-all text-xs">本标签页记录的上传编号：{receipt.transferId}。可复制编号和远端路径以便之后查询；本机路径不保存。</p>}
    {receipt && <div className="space-y-1">
      <label className="block text-xs">保存上传恢复记录<Textarea aria-label={`${host.name} 上传恢复记录`} readOnly value={recoveryRecord} rows={7} /></label>
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void copyRecovery()}>复制恢复记录</Button>
    </div>}
    <label className="block text-xs">粘贴已保存的恢复记录<Textarea aria-label={`${host.name} 粘贴恢复记录`} value={recoveryInput} disabled={busy} rows={4} onChange={(e) => setRecoveryInput(e.target.value)} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !recoveryInput.trim()} onClick={applyRecovery}>填入查询信息</Button>
    <p className="text-xs text-ink-2">恢复记录包含服务地址、主机和远端路径，请自行妥善保存；不包含私钥或本机文件路径。填入不会自动查询或上传，也不会替换本标签页的未决上传记录。</p>
    {busy && <p role="status">传输或查询进行中；请勿重复提交。</p>}{message && <p role="status" className="break-words text-xs">{message}</p>}
    {status && <p className="break-all text-xs">状态：{status.publication_state ?? "未知"} · 远端路径：{status.remote_path}{status.backup_path ? ` · 备份：${status.backup_path}` : ""}{status.stderr ? ` · 信息：${status.stderr}` : ""}</p>}
  </details>;
}
