import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostTransferResult, RemoteHost } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { HostFileBrowser } from "./HostFileBrowser";
import { HostBoundDownload } from "./HostBoundDownload";
import { HostTextPreview } from "./HostTextPreview";
import { HostServiceStart } from "./HostServiceStart";
import { HostConnectionRecovery } from "./HostConnectionRecovery";
import { downloadHostFile } from "../../ipc/hostDownload";
import { uploadHostFile } from "../../ipc/hostUpload";

type UploadReceipt = { hostSignature: string; remotePath: string; transferId: string; serviceId?: string; hostFingerprint?: string };
const signature = (host: RemoteHost) => JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port]);
const fingerprint = async (value: string) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))), (b) => b.toString(16).padStart(2, "0")).join("");
const isLocalAbsolute = (path: string) => /^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(path);
const isRemoteAbsolute = (path: string) => path.startsWith("/") && path !== "/" && !path.endsWith("/") && !path.includes("//") && !path.split("/").some((part) => part === ".." || part === ".");

export function HostFileTransfer({ host }: { host: RemoteHost }): JSX.Element {
  const [local, setLocal] = useState(""), [remote, setRemote] = useState("");
  const [transferOpen, setTransferOpen] = useState(false);
  const [browserFile, setBrowserFile] = useState<File | null>(null);
  const browserInput = useRef<HTMLInputElement | null>(null);
  const [selectedDirectory, setSelectedDirectory] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null), [status, setStatus] = useState<HostTransferResult | null>(null);
  const [manualId, setManualId] = useState(""), [manualRemote, setManualRemote] = useState("");
  const [recoveryInput, setRecoveryInput] = useState("");
  const [recoveryService, setRecoveryService] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0), browserDownload = useRef<AbortController | null>(null), base = ipc.agentBaseUrl, hostSignature = signature(host);
  const storageKey = `minimax:host-transfer:${base}:${host.id}`;
  useEffect(() => {
    epoch.current++; browserDownload.current?.abort(); setBrowserFile(null); setSelectedDirectory(null); setRemote(""); setLocal(""); if (browserInput.current) browserInput.current.value = ""; setBusy(false); setMessage(""); setStatus(null); setReceipt(null); setManualId(""); setManualRemote(""); setRecoveryInput(""); setRecoveryService(null);
    try { const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null") as UploadReceipt | null;
      const oldSignature = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file]);
      if (saved && (saved.hostSignature === hostSignature || saved.hostSignature === oldSignature) && /^[0-9a-f]{32}$/.test(saved.transferId) && isRemoteAbsolute(saved.remotePath)) {
        const restored = saved.hostSignature === hostSignature ? saved : { ...saved, hostSignature, serviceId: undefined, hostFingerprint: undefined };
        setReceipt(restored); setManualId(saved.transferId); setManualRemote(saved.remotePath); setRecoveryService(restored.serviceId ?? null);
      }
    } catch { /* Storage can be unavailable; no saved receipt is claimed. */ }
    return () => { epoch.current++; browserDownload.current?.abort(); };
  }, [storageKey, hostSignature, host.enabled]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; browserDownload.current?.abort(); setBrowserFile(null); setSelectedDirectory(null); setRemote(""); setLocal(""); if (browserInput.current) browserInput.current.value = ""; setBusy(false); setStatus(null); setRecoveryService(null); setManualId(""); setManualRemote(""); setMessage("连接已变化；请重新选择文件，或使用保存的恢复记录重新填入查询信息。"); }), []);
  const valid = (ticket: number) => epoch.current === ticket && ipc.agentBaseUrl === base;
  const recoveryRecord = receipt ? JSON.stringify({
    version: receipt.serviceId && receipt.hostFingerprint ? 2 : 1,
    service: base, host_id: host.id, hostname: host.hostname,
    username: host.username, port: host.port, remote_path: receipt.remotePath, transfer_id: receipt.transferId,
    ...(receipt.serviceId && receipt.hostFingerprint ? { service_id: receipt.serviceId, host_fingerprint: receipt.hostFingerprint } : {}),
  }, null, 2) : "";
  const copyRecovery = async () => {
    try { await navigator.clipboard.writeText(recoveryRecord); setMessage("恢复记录已复制，请自行保存。"); }
    catch { setMessage("无法访问剪贴板，请手动选择并复制下方恢复记录。"); }
  };
  const applyRecovery = async () => {
    const ticket = ++epoch.current;
    setRecoveryService(null); setStatus(null);
    try {
      if (recoveryInput.length > 8192) throw new Error("恢复记录过长");
      const saved: unknown = JSON.parse(recoveryInput);
      if (!saved || typeof saved !== "object" || Array.isArray(saved)) throw new Error("恢复记录格式无效");
      const record = saved as Record<string, unknown>;
      if (![1, 2].includes(record.version as number) || record.service !== base || record.host_id !== host.id ||
          record.hostname !== host.hostname || record.username !== host.username || record.port !== host.port) {
        throw new Error("记录与当前服务或主机连接信息不匹配");
      }
      if (typeof record.remote_path !== "string" || !isRemoteAbsolute(record.remote_path) ||
          typeof record.transfer_id !== "string" || !/^[0-9a-f]{32}$/.test(record.transfer_id)) {
        throw new Error("记录中的远端路径或传输编号无效");
      }
      if (record.version === 2) {
        const currentFingerprint = await fingerprint(hostSignature);
        const identity = await typedIPC.getServiceIdentity();
        if (!valid(ticket)) return;
        if (record.host_fingerprint !== currentFingerprint || record.service_id !== identity.service_id) throw new Error("恢复记录的服务身份或主机配置不匹配");
        setRecoveryService(identity.service_id);
      }
      setManualRemote(record.remote_path); setManualId(record.transfer_id); setStatus(null);
      setMessage(record.version === 2 ? "已核对服务身份和主机配置，请点击查询上传状态；未发起传输。" : "旧版记录无法核对服务身份或完整主机配置；请人工核对来源后查询。未发起传输。");
    } catch (e) { if (valid(ticket)) setMessage(e instanceof Error ? e.message : "恢复记录格式无效"); }
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
        setReceipt(next); setRecoveryService(null); setManualId(id); setManualRemote(next.remotePath);
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
  const browserTransfer = async () => {
    const path = remote.trim();
    if (!host.enabled || busy) return;
    if (!isRemoteAbsolute(path) || path.length > 4096 || path.includes("\0")) { setMessage("请输入有效的远端 Linux 绝对文件路径。"); return; }
    const ticket = ++epoch.current; setBusy(true); setMessage("");
    const controller = new AbortController(); browserDownload.current = controller;
    try {
      const confirmed = await requestConfirmation({ title: "确认下载到浏览器？", description: `通过 SSH 从 ${host.name} (${host.username}@${host.hostname}) 读取 ${path}，文件上限 10 MiB。浏览器将决定保存位置。`, confirmLabel: "确认下载" });
      if (!confirmed || !valid(ticket) || controller.signal.aborted) return;
      const blob = await downloadHostFile(host, path, base, controller.signal);
      if (!valid(ticket) || controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      try { const anchor = document.createElement("a"); anchor.href = url; anchor.download = (path.split("/").pop() || "download").replace(/[\\/:*?"<>|]/g, "_"); anchor.click(); }
      finally { URL.revokeObjectURL(url); }
      setMessage("文件字节已校验并交给浏览器下载；保存位置由浏览器决定。");
    } catch (e) { if (valid(ticket) && !controller.signal.aborted) setMessage(e instanceof Error ? e.message : String(e)); }
    finally { if (browserDownload.current === controller) browserDownload.current = null; if (valid(ticket)) setBusy(false); }
  };
  const browserUpload = async () => {
    const path = remote.trim(), file = browserFile;
    if (!host.enabled || busy || !file) return;
    if (receipt && !(status?.transfer_id === receipt.transferId && ["published", "aborted", "not_published"].includes(status.publication_state ?? ""))) {
      setMessage("上一笔上传尚未确认终态；请先查询状态并保存编号。"); return;
    }
    if (!isRemoteAbsolute(path) || path.length > 4096 || path.includes("\0") || file.size > 10 * 1024 * 1024) {
      setMessage("请输入有效远端绝对路径并选择不超过 10 MiB 的文件。"); return;
    }
    const ticket = ++epoch.current; setBusy(true); setMessage(""); setStatus(null);
    const controller = new AbortController(); browserDownload.current = controller;
    try {
      const confirmed = await requestConfirmation({ title: "确认上传浏览器文件？", description: `将 ${file.name} (${file.size} 字节) 上传到 ${host.name} (${host.username}@${host.hostname}) 的 ${path}。若目标存在，将在校验和未变化确认后替换并保留远端备份。`, confirmLabel: "确认上传" });
      if (!confirmed || !valid(ticket) || controller.signal.aborted) return;
      const { service_id } = await typedIPC.getServiceIdentity();
      const hostFingerprint = await fingerprint(hostSignature);
      if (!valid(ticket) || controller.signal.aborted) return;
      const id = crypto.randomUUID().replace(/-/g, "");
      const next = { hostSignature, remotePath: path, transferId: id, serviceId: service_id, hostFingerprint };
      try { sessionStorage.setItem(storageKey, JSON.stringify(next)); }
      catch { throw new Error("无法保存传输编号，未开始上传；请允许本标签页的会话存储。"); }
      setReceipt(next); setRecoveryService(service_id); setManualId(id); setManualRemote(path);
      const result = await uploadHostFile(host, path, id, file, base, controller.signal, service_id);
      if (!valid(ticket)) return;
      setStatus(result); setMessage(result.ok && result.publication_state === "published" ? "上传已确认发布并校验。" : "上传未确认完成；请先查询状态，避免重复上传。");
    } catch (e) { if (valid(ticket)) setMessage(`${e instanceof Error ? e.message : String(e)}；结果可能不明，请先查询传输状态。`); }
    finally { if (browserDownload.current === controller) browserDownload.current = null; if (valid(ticket)) setBusy(false); }
  };
  const inspect = async () => {
    if (!host.enabled || !isRemoteAbsolute(manualRemote.trim()) || !/^[0-9a-f]{32}$/.test(manualId)) { setMessage("请输入远端绝对路径和 32 位小写十六进制传输编号。"); return; }
    const ticket = ++epoch.current; setBusy(true); setMessage(""); setStatus(null);
    try { const identity = await typedIPC.getServiceIdentity();
      if (!valid(ticket)) return;
      if (recoveryService && recoveryService !== identity.service_id) throw new Error("恢复记录所属服务身份已变化");
      const result = await typedIPC.getBoundHostTransferStatus({ host_id: host.id, expected_service_id: identity.service_id,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, remote_path: manualRemote.trim(), transfer_id: manualId });
      if (!valid(ticket)) return;
      const after = await typedIPC.getServiceIdentity();
      if (!valid(ticket)) return;
      if (after.service_id !== identity.service_id) throw new Error("服务身份已变化");
      if (result.transfer_id !== manualId || result.remote_path !== manualRemote.trim()) throw new Error("状态回执与请求不匹配");
      setStatus(result); setMessage(result.ok ? "已读取远端上传记录；状态不代表其他文件操作。" : "状态未确认；请检查连接或远端记录，不要自动重试上传。");
    } catch (e) { if (valid(ticket)) setMessage(`${e instanceof Error ? e.message : String(e)}；上传结果仍未知。`); }
    finally { if (valid(ticket)) setBusy(false); }
  };
  return <><HostConnectionRecovery host={host} /><HostServiceStart host={host} /><details className="rounded border border-line p-2" onToggle={(event) => setTransferOpen(event.currentTarget.open)}><summary>上传 / 下载文件</summary>
    <p className="text-xs text-ink-2">本机路径指运行当前服务的机器，不是浏览器选中的文件；远端路径指此主机的 Linux 绝对路径。仅单文件、显式操作，不自动同步。传输使用独立 SSH/SFTP，服务隧道状态不代表传输可用。</p>
    <label className="block text-xs">本机文件绝对路径<Input aria-label={`${host.name} 本机传输路径`} value={local} disabled={busy} onChange={(e) => setLocal(e.target.value)} placeholder="C:\\资料\\报告.pdf" /></label>
    <label className="block text-xs">远端文件绝对路径<Input aria-label={`${host.name} 远端传输路径`} value={remote} disabled={busy} onChange={(e) => setRemote(e.target.value)} placeholder="/home/user/report.pdf" /></label>
    <label className="block text-xs">选择浏览器文件（最大 10 MiB）<input ref={browserInput} type="file" aria-label={`${host.name} 浏览器上传文件`} disabled={busy} onChange={(e) => setBrowserFile(e.target.files?.[0] ?? null)} /></label>
    <HostFileBrowser host={host} onSelect={(path) => { if (!busy) setRemote(path); }} onSelectDirectory={(path) => { if (!busy) { setSelectedDirectory(path); setMessage("目录已选择；请选择浏览器文件并显式填入目标路径。"); } }} />
    {selectedDirectory && <div className="space-y-1 text-xs"><p className="break-all">已选择上传目录：{selectedDirectory}</p>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !browserFile} onClick={() => {
        if (!browserFile) return;
        const name = browserFile.name;
        if (!name || name === "." || name === ".." || /[\/\\]|\p{Cc}|\p{Cf}|\p{Cs}/u.test(name)) { setMessage("文件名不适合作为远端单个文件名，请手动输入并核对路径。"); return; }
        const target = `${selectedDirectory === "/" ? "" : selectedDirectory}/${name}`;
        if (target.length > 4096 || !isRemoteAbsolute(target)) { setMessage("目标路径无效或超过 4096 字符。"); return; }
        setRemote(target); setMessage("已填入目标路径；请核对后确认上传。若目标存在，上传会在校验后替换并保留备份。");
      }}>用所选目录和文件名填入目标</Button>
      <p>仅填入路径，上传仍需单独确认；同名目标可能被替换并保留备份。</p></div>}
    <HostBoundDownload host={host} remotePath={remote.trim()} active={transferOpen} />
    <HostTextPreview host={host} path={remote.trim()} active={transferOpen} />
    <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy || !host.enabled} onClick={() => void transfer("upload")}>上传到此主机</Button>
      <Button type="button" size="sm" disabled={busy || !host.enabled || !browserFile} onClick={() => void browserUpload()}>上传浏览器文件</Button>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void transfer("download")}>下载到服务端路径</Button>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void browserTransfer()}>下载到当前浏览器</Button>
      {busy && browserDownload.current && <Button type="button" size="sm" variant="secondary" onClick={() => browserDownload.current?.abort()}>取消浏览器传输</Button>}</div>
    <p className="text-xs text-ink-2">浏览器下载通过 SSH 执行远端只读命令，仅支持不经过符号链接的普通文件，最大 10 MiB；不写入服务端文件。</p>
    <p className="text-xs text-ink-2">上传备份位于远端传输记录目录；服务端路径下载只创建新文件，不覆盖已有文件。浏览器下载的保存位置和同名处理由浏览器决定。校验不能证明业务内容正确；响应中断时先核对，不自动重试。</p>
    <label className="block text-xs">上传传输编号<Input aria-label={`${host.name} 上传传输编号`} value={manualId} maxLength={32} disabled={busy} onChange={(e) => { setManualId(e.target.value); setRecoveryService(null); setStatus(null); }} /></label>
    <label className="block text-xs">查询用远端路径<Input aria-label={`${host.name} 上传查询路径`} value={manualRemote} disabled={busy} onChange={(e) => { setManualRemote(e.target.value); setRecoveryService(null); setStatus(null); }} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !host.enabled} onClick={() => void inspect()}>查询上传状态</Button>
    {receipt && <p className="break-all text-xs">本标签页记录的上传编号：{receipt.transferId}。可复制编号和远端路径以便之后查询；本机路径不保存。</p>}
    {receipt && <div className="space-y-1">
      <label className="block text-xs">保存上传恢复记录<Textarea aria-label={`${host.name} 上传恢复记录`} readOnly value={recoveryRecord} rows={7} /></label>
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void copyRecovery()}>复制恢复记录</Button>
    </div>}
    <label className="block text-xs">粘贴已保存的恢复记录<Textarea aria-label={`${host.name} 粘贴恢复记录`} value={recoveryInput} disabled={busy} rows={4} onChange={(e) => setRecoveryInput(e.target.value)} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !recoveryInput.trim()} onClick={() => void applyRecovery()}>填入查询信息</Button>
    <p className="text-xs text-ink-2">浏览器上传生成的新版恢复记录包含服务身份、主机配置摘要和远端路径；旧版记录须人工核对来源。记录不包含私钥或本机文件路径，请妥善保存。填入不会自动查询或上传，也不会替换本标签页的未决上传记录。</p>
    {busy && <p role="status">传输或查询进行中；请勿重复提交。</p>}{message && <p role="status" className="break-words text-xs">{message}</p>}
    {status && <p className="break-all text-xs">状态：{status.publication_state ?? "未知"} · 远端路径：{status.remote_path}{status.backup_path ? ` · 备份：${status.backup_path}` : ""}{status.stderr ? ` · 信息：${status.stderr}` : ""}</p>}
  </details></>;
}
