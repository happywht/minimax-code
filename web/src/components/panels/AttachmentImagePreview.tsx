import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";

const LIMIT = 5 * 1024 * 1024;
const MIME: Record<string, string> = { png: "image/png", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp" };

/** Display only explicitly requested, identity-checked original bytes. */
export function AttachmentImagePreview({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element | null {
  const [url, setUrl] = useState<string | null>(null);
  const [loadedScope, setLoadedScope] = useState<string | null>(null), [nativeSize, setNativeSize] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), pending = useRef<AbortController | null>(null), resource = useRef<string | null>(null);
  const eligible = !!MIME[attachment.recognized_type] && attachment.declared_type === attachment.recognized_type;
  const scope = !!project && !!session && !!service && project === attachment.project_id && service === attachment.service_id;
  const scopeKey = JSON.stringify([project, session, service, host, attachment.id, attachment.sha256, attachment.size_bytes, attachment.recognized_type, attachment.declared_type]);
  const visibleUrl = scope && loadedScope === scopeKey ? url : null;

  const dispose = () => {
    ++epoch.current; pending.current?.abort(); pending.current = null;
    if (resource.current) URL.revokeObjectURL(resource.current);
    resource.current = null; setUrl(null); setLoadedScope(null); setNativeSize(false); setBusy(false); setError("");
  };
  useEffect(() => {
    dispose();
    return () => { ++epoch.current; pending.current?.abort(); if (resource.current) URL.revokeObjectURL(resource.current); resource.current = null; };
  // Identity and connection changes invalidate both bytes and pending requests.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, session, service, host, attachment.id, attachment.sha256, attachment.size_bytes, attachment.recognized_type, attachment.declared_type]);
  useEffect(() => ipc.onSideCar(() => dispose()), []);

  const open = async () => {
    if (!scope || !eligible || busy || ipc.isMock || attachment.size_bytes > LIMIT) return;
    dispose();
    const ticket = epoch.current, controller = new AbortController(); pending.current = controller; setBusy(true);
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && !controller.signal.aborted && ipc.agentBaseUrl === host &&
        sessions.currentProjectId === project && sessions.currentSessionId === session && attachments.projectId === project &&
        attachments.host === host && attachments.serviceId === service && attachments.selected?.id === attachment.id &&
        attachments.selected.sha256 === attachment.sha256;
    };
    try {
      const original = await fetchAttachmentContent(attachment, host, controller.signal);
      if (!current()) return;
      const next = URL.createObjectURL(new Blob([original], { type: MIME[attachment.recognized_type] }));
      resource.current = next; setUrl(next); setLoadedScope(scopeKey);
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (current()) { pending.current = null; setBusy(false); } }
  };

  if (!eligible) return null;
  return <section className="space-y-2 rounded border border-line p-2 text-xs" aria-label="图片原件预览">
    <p>浏览器显示经过字节大小和 SHA-256 校验的原件；动画图片可能自动播放。显示内容不代表业务或视觉质量已验收。</p>
    {!scope && <p role="status">请在当前项目选择会话后查看原件。</p>}
    {attachment.size_bytes > LIMIT && <p role="status">图片超过 5 MiB 预览上限；可下载完整原件。</p>}
    {ipc.isMock && <p role="status">演示模式不提供图片原件预览。</p>}
    <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" loading={busy}
      disabled={!scope || busy || ipc.isMock || attachment.size_bytes > LIMIT} onClick={() => void open()}>查看图片原件</Button>
      {visibleUrl && <Button type="button" size="sm" variant="secondary" onClick={() => setNativeSize(!nativeSize)}>{nativeSize ? "适应面板" : "原始尺寸"}</Button>}
      {visibleUrl && <Button type="button" size="sm" variant="secondary" onClick={dispose}>关闭图片预览</Button>}</div>
    {error && <p role="alert">图片预览失败：{error}</p>}
    {visibleUrl && <div className="max-h-[70vh] overflow-auto rounded border border-line bg-surface-1">
      <img src={visibleUrl} alt={`附件原件：${attachment.original_name}`} className={nativeSize ? "h-auto max-w-none" : "h-auto max-w-full"}
        onError={() => { dispose(); setError("浏览器无法解码图片，请下载原件核对。"); }} />
    </div>}
  </section>;
}
