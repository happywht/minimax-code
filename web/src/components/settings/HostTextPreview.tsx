import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { downloadHostFile } from "../../ipc/hostDownload";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { HostPreviewAttachment } from "./HostPreviewAttachment";

const PREVIEW_BYTES = 64 * 1024;
const validPath = (value: string) => value.startsWith("/") && value !== "/" && value.length <= 4096 &&
  !value.includes("\0") && !value.endsWith("/") && !value.split("/").slice(1).some((part) => !part || part === "." || part === "..");

/** A temporary text preview obtained through the authenticated, checked download path. */
export function HostTextPreview({ host, path, active }: { host: RemoteHost; path: string; active: boolean }): JSX.Element {
  const [preview, setPreview] = useState<{ path: string; text: string; truncated: boolean; filtered: boolean; sha256: string; serviceId: string; blob: Blob } | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [destination, setDestination] = useState("");
  const saving = useRef(false);
  const controller = useRef<AbortController | null>(null), epoch = useRef(0);
  const base = ipc.agentBaseUrl;
  const scope = JSON.stringify([base, host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled, path, active]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  useEffect(() => {
    epoch.current++; controller.current?.abort(); controller.current = null;
    setPreview(null); setBusy(false); setMessage(saving.current ? "保存请求可能仍在处理；请检查目标文件，不要直接重试。" : "");
    return () => { epoch.current++; controller.current?.abort(); controller.current = null; };
  }, [scope]);
  useEffect(() => ipc.onSideCar(() => {
    epoch.current++; controller.current?.abort(); controller.current = null;
    setPreview(null); setBusy(false); setMessage(saving.current ? "连接已变化；保存结果可能未知，请先检查目标文件。" : "连接已变化，请重新选择并读取文件。");
  }), []);

  const read = async () => {
    if (controller.current || saving.current || !active || !host.enabled || !validPath(path)) return;
    const abort = new AbortController(), ticket = ++epoch.current, snapshot = scope;
    controller.current = abort; setBusy(true); setPreview(null); setMessage("");
    const current = () => !abort.signal.aborted && epoch.current === ticket && currentScope.current === snapshot && ipc.agentBaseUrl === base;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const blob = await downloadHostFile(host, path, base, abort.signal);
      if (!current()) return;
      const bytes = new Uint8Array(await blob.arrayBuffer());
      if (!current()) return;
      // Validate the entire file as UTF-8 and reject binary NUL before showing a prefix.
      let full: string;
      try { full = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
      catch { throw new Error("文件不是有效 UTF-8 文本，无法预览。"); }
      if (full.includes("\0")) throw new Error("文件包含二进制 NUL，不能作为文本预览");
      // Streaming decode omits an incomplete multibyte character at the boundary.
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, PREVIEW_BYTES), { stream: true });
      const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== identity.service_id) throw new Error("服务身份已变化，请重新预览。");
      const safe = text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, "");
      if (current()) setPreview({ path, text: safe, truncated: bytes.length > PREVIEW_BYTES, filtered: safe !== text, sha256, serviceId: identity.service_id, blob });
    } catch (error) {
      if (current()) setMessage(error instanceof Error ? error.message : "读取失败");
    } finally {
      if (controller.current === abort) controller.current = null;
      if (current()) setBusy(false);
    }
  };
  const save = async () => {
    const selected = preview, target = destination.trim();
    if (!selected || busy || saving.current || !/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(target)) return;
    saving.current = true; setBusy(true); setMessage("");
    const ticket = epoch.current, snapshot = scope;
    const current = () => epoch.current === ticket && currentScope.current === snapshot && ipc.agentBaseUrl === base;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== selected.serviceId) throw new Error("服务身份已变化，请重新预览。");
      const accepted = await requestConfirmation({ title: "保存预览版本？", description: `从 ${host.username}@${host.hostname} 再次读取 ${selected.path}，仅在完整文件 SHA-256 与预览一致时创建运行当前服务的机器上的 ${target}。已有目标不会覆盖。`, confirmLabel: "确认保存" });
      if (!accepted || !current()) return;
      const result = await typedIPC.saveHostPreview({ host_id: host.id, expected_service_id: selected.serviceId,
        hostname: host.hostname, username: host.username, port: host.port, identity_file: host.identity_file,
        remote_port: host.remote_port, remote_path: selected.path, local_path: target, expected_sha256: selected.sha256 });
      if (!current()) return;
      setMessage(result.ok ? "已保存与预览哈希一致的完整文件。请核对内容；校验不代表业务正确。" :
        result.reason === "changed" ? "远端文件已变化；未保存，请重新预览。" : "目标已存在；未覆盖，请选择新路径。");
    } catch { if (current()) setMessage("保存未确认；请先检查目标文件是否已创建，不要直接重试。"); }
    finally { saving.current = false; if (current()) setBusy(false); }
  };
  const clear = () => { epoch.current++; controller.current?.abort(); controller.current = null; setBusy(false); setPreview(null); setMessage(""); };
  return <div className="space-y-1 rounded border border-line p-2">
    <p className="text-xs">远端文本预览</p>
    <p className="text-xs text-ink-2">仅在点击后通过认证连接读取当前远端路径的整个文件（最大 10 MiB），校验后仅显示前 64 KiB UTF-8 文本，不保存文件。内容可能包含凭据，请按需查看；预览后文件仍可能变化。</p>
    <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || saving.current || !host.enabled || !validPath(path)} onClick={() => void read()}>读取所选文件预览</Button>
      {(!saving.current && (busy || preview)) && <Button type="button" size="sm" variant="secondary" onClick={clear}>{busy ? "取消读取" : "清除预览"}</Button>}</div>
    {message && <p role="status" className="text-xs text-status-error">{message}</p>}
    {preview && <div className="text-xs"><p className="break-all">{preview.path}{preview.truncated ? " · 已截断" : ""}{preview.filtered ? " · 已移除部分控制字符" : ""}</p>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{preview.text || "（空文件）"}</pre>
      <p className="break-all">完整文件 SHA-256：{preview.sha256}</p>
      <label className="block">保存到运行当前服务的机器上的新文件（绝对路径）<Input aria-label={`${host.name} 预览版本保存路径`} value={destination} disabled={busy} onChange={(event) => setDestination(event.target.value)} /></label>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(destination.trim())} onClick={() => void save()}>保存预览版本</Button>
      <p>保存时重新读取完整文件，仅哈希一致且目标不存在时创建；目标路径属于运行当前服务的机器。</p></div>}
    <HostPreviewAttachment host={host} preview={preview} active={active} />
  </div>;
}
