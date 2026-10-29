import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentDocumentCursor, AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";

/** Copy the reader's entire supported text range into a new, editable plain body. */
export function ReportDocumentReuse({ attachment, project, session, service, host, draftVersion, onApply }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null;
  host: string; draftVersion: string; onApply: (text: string) => void;
}): JSX.Element | null {
  const [candidate, setCandidate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), draft = useRef(draftVersion); draft.current = draftVersion;
  useEffect(() => {
    const invalidate = () => { ++epoch.current; setCandidate(null); setBusy(false); };
    invalidate(); setNotice("");
    const offSession = useSessionStore.subscribe((s) => { if (s.currentProjectId !== project || s.currentSessionId !== session) invalidate(); });
    const offAttachment = useAttachmentStore.subscribe((s) => {
      if (s.projectId !== project || s.host !== host || s.serviceId !== service ||
          s.selected?.id !== attachment.id || s.selected.sha256 !== attachment.sha256) invalidate();
    });
    const offConnection = ipc.onSideCar(invalidate);
    return () => { ++epoch.current; offSession(); offAttachment(); offConnection(); };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  const eligible = (attachment.recognized_type === "docx" || attachment.recognized_type === "pdf") && attachment.declared_type === attachment.recognized_type;
  if (!eligible) return null;
  const current = (ticket: number) => {
    const s = useSessionStore.getState(), a = useAttachmentStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && s.currentProjectId === project && s.currentSessionId === session &&
      a.projectId === project && a.host === host && a.serviceId === service && a.selected?.id === attachment.id && a.selected.sha256 === attachment.sha256;
  };
  const readAll = async (ticket: number): Promise<string> => {
    if (!project || !session || !service || !current(ticket)) throw new Error("项目、会话或服务已变化，请重新选择。");
    let cursor: AttachmentDocumentCursor | null = { unit: 1, offset: 0 }, total: number | null = null;
    const units: string[] = [];
    for (let calls = 0; cursor; calls++) {
      if (calls >= 100) throw new Error("可读取范围超过 100 次读取上限，草稿未更改；请使用分段摘录。");
      const requested: AttachmentDocumentCursor = cursor;
      const result = await typedIPC.readAttachmentDocument({ project_id: project, session_id: session, expected_service_id: service,
        attachment_id: attachment.id, expected_sha256: attachment.sha256, ...requested });
      if (!current(ticket)) throw new Error("上下文已变化，请重新选择附件。");
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service ||
          result.attachment_id !== attachment.id || result.sha256 !== attachment.sha256 || result.kind !== attachment.recognized_type ||
          result.start !== requested.offset || (result.unit !== requested.unit && result.unit_total !== 0) ||
          (total !== null && total !== result.unit_total)) throw new Error("附件身份或读取范围发生变化。");
      total = result.unit_total;
      if (result.unit > 0) units[result.unit - 1] = (units[result.unit - 1] ?? "") + result.text;
      cursor = result.next_cursor;
      if (cursor && !((cursor.unit === result.unit && cursor.offset === result.end && result.end > result.start) ||
          (cursor.unit === result.unit + 1 && cursor.offset === 0 && !result.truncated))) throw new Error("读取位置无效，草稿未更改。");
    }
    // Reader units are paragraphs for DOCX and physical pages for PDF. Preserve their order,
    // but collapse internal whitespace because each editor line is one paragraph.
    const paragraphs = units.map((value) => value.replace(/\s+/g, " ").trim()).filter(Boolean);
    const text = paragraphs.join("\n");
    // eslint-disable-next-line no-control-regex
    if (!text || paragraphs.length > 100 || paragraphs.some((value) => Array.from(value).length > 4096 || /[\x00-\x08\x0b-\x1f\x7f-\x9f\ufffe\uffff]/.test(value)) ||
        new TextEncoder().encode(text).length > 256 * 1024) throw new Error("可读取文字为空或超过报告限制（100 段、每段 4096 字、总量 256 KiB），草稿未更改。");
    return text;
  };
  const prepare = async () => {
    if (busy) return;
    const ticket = ++epoch.current; setBusy(true); setCandidate(null); setNotice("");
    try { const text = await readAll(ticket); if (current(ticket)) setCandidate(text); }
    catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const apply = async () => {
    if (!candidate || busy) return;
    const ticket = ++epoch.current, expectedDraft = draft.current; setBusy(true); setNotice("");
    try {
      const text = await readAll(ticket);
      if (!current(ticket)) return;
      if (text !== candidate) throw new Error("读取内容已变化，请重新准备。");
      if (draft.current !== expectedDraft) throw new Error("报告草稿在核对期间发生变化，请重新确认。");
      onApply(text); setCandidate(null); setNotice("已替换普通正文并切换至普通段落；原章节及来源仍保留，请核对正文和引用。尚未生成文件。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p>将文档可读取范围的文字转为可编辑正文。DOCX 按正文及表格内段落，PDF 按物理页提取文字；合并单元内空白，不保留表格结构、版式、图片、页眉页脚或其他未提取内容，也不做 OCR。请对照原件核对，原件不变。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !session || !service || ipc.isMock} onClick={() => void prepare()}>{busy ? "正在读取…" : "准备文档文字到报告"}</Button>
    {candidate !== null && <div className="space-y-2"><p>已读取接口支持的全部范围，非原件完整内容。确认将替换普通正文并切换至普通段落；现有结构化章节、标题和来源保留，但不会自动关联文字。请先核对：</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words">{candidate}</pre>
      <Button type="button" disabled={busy} onClick={() => void apply()}>确认替换普通正文</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => { setCandidate(null); setNotice("已取消，草稿未更改。"); }}>取消</Button></div>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}
