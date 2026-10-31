import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";

const PREVIEW_LIMIT = 5 * 1024 * 1024;

/** Explicit native browser viewer; PDF contents are not a security boundary. */
export function PdfOriginalPreview({ attachment }: { attachment: AttachmentOriginal }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.currentProjectId) ?? "inbox";
  const host = ipc.agentBaseUrl;
  const [url, setUrl] = useState<string | null>(null);
  const [loadedScope, setLoadedScope] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const version = useRef(0), active = useRef<AbortController | null>(null), resource = useRef<string | null>(null);
  const eligible = attachment.declared_type === "pdf" && attachment.recognized_type === "pdf";
  const scope = !!session && project === attachment.project_id;
  const parsed = ["parsed", "truncated", "empty"].includes(attachment.parser_status);
  const scopeKey = JSON.stringify([host, session, project, attachment.id, attachment.sha256, attachment.service_id,
    attachment.size_bytes, attachment.declared_type, attachment.recognized_type, attachment.parser_status]);
  const visibleUrl = scope && loadedScope === scopeKey ? url : null;
  const dispose = () => {
    ++version.current; active.current?.abort(); active.current = null;
    if (resource.current) URL.revokeObjectURL(resource.current);
    resource.current = null; setUrl(null); setLoadedScope(null); setLoading(false); setError(null);
  };
  useEffect(() => {
    const token = version, pending = active, objectUrl = resource;
    dispose();
    return () => { ++token.current; pending.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); objectUrl.current = null; };
  // A document, project, session or host change must release the old bytes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachment.id, attachment.sha256, attachment.service_id, attachment.size_bytes, attachment.declared_type,
    attachment.recognized_type, attachment.parser_status, session, project, host]);
  useEffect(() => typeof ipc.onSideCar === "function" ? ipc.onSideCar(() => dispose()) : undefined, []);

  const open = async () => {
    if (!scope || !parsed || attachment.size_bytes > PREVIEW_LIMIT || ipc.isMock) return;
    dispose(); const ticket = version.current, base = ipc.agentBaseUrl;
    const controller = new AbortController(); active.current = controller; setLoading(true);
    const current = () => ticket === version.current && !controller.signal.aborted && ipc.agentBaseUrl === base
      && useSessionStore.getState().currentSessionId === session
      && (useSessionStore.getState().currentProjectId ?? "inbox") === project;
    try {
      if (typeof navigator.pdfViewerEnabled === "boolean" && !navigator.pdfViewerEnabled)
        throw new Error("浏览器不支持内置 PDF 查看器，请下载原件查看。");
      const original = await fetchAttachmentContent(attachment, base, controller.signal);
      if (!current()) return;
      if (await original.slice(0, 5).text() !== "%PDF-") throw new Error("原件不是可识别的 PDF，请下载核对。");
      if (!current()) return;
      const next = URL.createObjectURL(new Blob([original], { type: "application/pdf" }));
      resource.current = next; setUrl(next); setLoadedScope(scopeKey);
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (current()) setLoading(false); }
  };

  if (!eligible) return null;
  return <div className="space-y-2 rounded border border-line p-2 text-xs">
    <p>原件由浏览器内置 PDF 查看器解析，并非隔离环境；文档可能包含外链或交互，查看器可能访问外部资源。</p><p>内容和版式未经过可信性或视觉验收。</p>
    {!scope && <p role="status">请在当前项目选择会话后查看原件。</p>}
    {!parsed && <p role="status">PDF 文本解析不可用或失败，无法在此预览；可下载原件。</p>}
    {attachment.size_bytes > PREVIEW_LIMIT && <p role="status">PDF 超过 5 MiB 预览上限；可下载完整原件。</p>}
    {ipc.isMock && <p role="status">演示模式不提供 PDF 原件预览。</p>}
    <Button type="button" size="sm" variant="secondary" loading={loading}
      disabled={!scope || !parsed || attachment.size_bytes > PREVIEW_LIMIT || ipc.isMock || loading} onClick={() => void open()}>查看 PDF 原件</Button>
    {visibleUrl && <Button type="button" size="sm" variant="secondary" onClick={dispose}>关闭 PDF 预览</Button>}
    {error && <p role="alert">{error}</p>}
    {visibleUrl && <><p>若预览为空或浏览器不支持内置查看器，请下载原件。</p>
      <iframe title="PDF 原件预览" src={visibleUrl} referrerPolicy="no-referrer" className="h-[min(70vh,640px)] w-full rounded border border-line" /></>}
  </div>;
}
