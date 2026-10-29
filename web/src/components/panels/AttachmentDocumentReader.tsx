import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button, Input } from "../../ui";
import type { AttachmentDocumentCursor, AttachmentDocumentResult, AttachmentOriginal } from "../../types/ipc";

export function AttachmentDocumentReader({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element | null {
  const [result, setResult] = useState<AttachmentDocumentResult | null>(null);
  const [unit, setUnit] = useState("1"), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const eligible = (attachment.recognized_type === "pdf" || attachment.recognized_type === "docx") &&
    attachment.declared_type === attachment.recognized_type;
  useEffect(() => {
    ++epoch.current; setResult(null); setError(""); setBusy(false); setUnit("1");
    return () => { ++epoch.current; };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setResult(null); setBusy(false); setError("连接变化，请重新选择附件。"); }), []);
  if (!eligible) return null;
  const read = async (cursor: AttachmentDocumentCursor) => {
    if (!project || !session || !service || busy || ipc.isMock) return;
    const ticket = ++epoch.current, id = attachment.id, sha = attachment.sha256;
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && sessions.currentProjectId === project &&
        sessions.currentSessionId === session && attachments.projectId === project && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === sha;
    };
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await typedIPC.readAttachmentDocument({ project_id: project, session_id: session,
        expected_service_id: service, attachment_id: id, expected_sha256: sha, ...cursor });
      if (!current()) return;
      if (response.project_id !== project || response.session_id !== session || response.service_id !== service ||
          response.attachment_id !== id || response.sha256 !== sha || response.kind !== attachment.recognized_type ||
          response.start !== cursor.offset || (response.unit !== cursor.unit && response.unit_total !== 0))
        throw new Error("返回的附件身份或位置不匹配，请重新选择。");
      setResult(response);
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const number = Number(unit);
  return <div className="space-y-2 rounded border border-line p-3" aria-label="文档分段读取">
    <h4 className="font-medium">手动读取文档片段</h4>
    <p className="text-xs text-ink-1">{attachment.recognized_type === "pdf"
      ? "按 PDF 物理页读取已有文字，不还原版式；扫描图像未尝试 OCR。"
      : "按正文和表格段落读取，不含页眉页脚、脚注、修订、文本框及图像文字；未尝试 OCR。"}空文本不代表原件无内容，内容未经核验。每次最多 4000 字符，不自动加入对话。</p>
    <div className="flex flex-wrap gap-2"><Input aria-label={attachment.recognized_type === "pdf" ? "物理页序号" : "正文段落序号"}
      type="number" min={1} max={attachment.recognized_type === "pdf" ? 200 : 10000} value={unit} onChange={(event) => setUnit(event.target.value)} />
      <Button type="button" variant="secondary" disabled={!project || !session || !service || busy || ipc.isMock || !Number.isInteger(number) || number < 1 || number > (attachment.recognized_type === "pdf" ? 200 : 10000)} onClick={() => void read({ unit: number, offset: 0 })}>读取指定位置</Button>
      {result?.next_cursor && <Button type="button" variant="secondary" disabled={busy} onClick={() => void read(result.next_cursor!)}>读取下一片段</Button>}</div>
    {ipc.isMock && <p role="status">演示模式不支持文档分段读取。</p>}
    {busy && <p role="status">正在读取…</p>}
    {error && <p role="alert">读取失败：{error}</p>}
    {result && <div><p className="text-xs text-ink-1">来源：{attachment.original_name} · {attachment.id} · SHA-256 {attachment.sha256} · 位置 {result.location} · 单元 {result.unit}/{result.unit_total} · 字符 {result.start}–{result.end} · {result.status === "empty" ? "未提取到文字" : "已提取文字"} · OCR 未尝试</p>
      {result.truncated && <p role="status">此位置的文字尚未读完，请读取下一片段。</p>}
      <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3">{result.text || "（此范围未提取到文字）"}</pre>
      {!result.next_cursor && <p>已到可读取范围末尾；不代表覆盖原件全部内容。</p>}</div>}
  </div>;
}
