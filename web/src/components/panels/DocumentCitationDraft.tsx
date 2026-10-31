import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentGetResult, AttachmentDocumentResult, DocumentExcerptRef } from "../../types/ipc";
import { Button } from "../../ui";

export type CitationDraftItem = { number: number; name: string; version: number; id: string; sha256: string;
  kind: "pdf" | "docx" | "txt" | "csv"; unit: number; location: string; start: number; end: number; text: string; truncated: boolean };
export function DocumentCitationDraft({ citations, project, session, service, host, draftVersion, onApply }: {
  citations: { ref: DocumentExcerptRef; label: string }[]; project: string | null; session: string | null; service: string | null; host: string;
  draftVersion: string; onApply: (items: CitationDraftItem[]) => boolean;
}): JSX.Element {
  const [selected, setSelected] = useState<number[]>([]), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [candidate, setCandidate] = useState<{ items: CitationDraftItem[]; revision: number; indices: number[] } | null>(null);
  const epoch = useRef(0), draft = useRef({ value: draftVersion, revision: 0 }), callback = useRef(onApply);
  callback.current = onApply;
  if (draft.current.value !== draftVersion) draft.current = { value: draftVersion, revision: draft.current.revision + 1 };
  const citationKey = JSON.stringify(citations);
  useEffect(() => { epoch.current++; setSelected([]); setCandidate(null); setBusy(false); setNotice(""); return () => { epoch.current++; }; }, [project, session, service, host, citationKey]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setSelected([]); setCandidate(null); setBusy(false); setNotice("连接变化，请重新选择引用。"); }), []);
  const prepare = async (confirm: boolean) => {
    if (!project || !session || !service || busy || ipc.isMock || (confirm && !candidate)) return;
    const indices = confirm ? candidate!.indices : [...selected].sort((a, b) => a - b);
    if (!indices.length || indices.length > 10) return;
    const revision = confirm ? candidate!.revision : draft.current.revision, ticket = ++epoch.current;
    const current = () => {
      const active = useSessionStore.getState(), store = useAttachmentStore.getState();
      return ticket === epoch.current && draft.current.revision === revision && ipc.agentBaseUrl === host &&
        active.currentProjectId === project && active.currentSessionId === session && store.projectId === project && store.host === host && store.serviceId === service;
    };
    setBusy(true); setNotice("");
    try {
      if (!current()) throw new Error("报告或归属已变化，请重新准备");
      const items: CitationDraftItem[] = [];
      for (const index of indices) {
        const ref = citations[index]?.ref;
        if (!ref || !Number.isInteger(ref.unit) || !Number.isInteger(ref.start) || !Number.isInteger(ref.end) ||
            ref.unit < 1 || ref.start < 0 || ref.end <= ref.start || ref.end - ref.start > 1000) throw new Error("资料引用无效，请重新选择");
        const metadata: AttachmentGetResult = await typedIPC.getAttachment(project, ref.attachment_id);
        const result: AttachmentDocumentResult = await typedIPC.readAttachmentDocument({ project_id: project,
          session_id: session, expected_service_id: service, attachment_id: ref.attachment_id, expected_sha256: ref.expected_sha256, unit: ref.unit, offset: ref.start });
        if (!current()) throw new Error("报告或归属已变化，请重新准备");
        const row = metadata.attachment;
        if (row.id !== ref.attachment_id || row.project_id !== project || row.service_id !== service || row.sha256 !== ref.expected_sha256 ||
            result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== ref.attachment_id ||
            result.sha256 !== ref.expected_sha256 || result.unit !== ref.unit || result.start !== ref.start || result.end < ref.end ||
            row.declared_type !== result.kind || !(row.recognized_type === result.kind || (row.recognized_type === "utf8_text" && ["txt", "csv"].includes(result.kind))) ||
            Array.from(result.text).length !== result.end - result.start || result.end - result.start > 4000 ||
            Array.from(result.text).length < ref.end - ref.start) throw new Error("附件身份或选段已变化，请重新选择");
        const text = Array.from(result.text).slice(0, ref.end - ref.start).join("");
        if (!text.trim()) throw new Error(`资料 ${index + 1} 的选段没有可用文字`);
        items.push({ number: index + 1, name: row.original_name, version: row.version, id: row.id, sha256: row.sha256, kind: result.kind,
          unit: result.unit, location: result.location, start: ref.start, end: ref.end, text,
          truncated: ref.start > 0 || ref.end < result.end || result.truncated });
      }
      if (!current()) throw new Error("报告或归属已变化，请重新准备");
      if (!confirm) { setCandidate({ items, revision, indices }); return; }
      if (JSON.stringify(candidate!.items) !== JSON.stringify(items)) throw new Error("附件内容或位置已变化，请重新准备");
      if (!callback.current(items)) throw new Error("未追加；请检查目标章节、字符和报告长度限制");
      setCandidate(null); setSelected([]); setNotice("已追加资料摘录初稿，尚未生成文件；请核对引用、内容和事实。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <details className="space-y-2 rounded border border-line p-2"><summary>根据已选资料引用准备初稿</summary>
    <p>勾选已加入报告的资料引用，可跨附件和位置整理。先复读并预览，确认时再次核对；只追加选段及待填写的研究框架，不推断事实，不修改原件。</p>
    {citations.map((item, index) => <label key={`${item.ref.attachment_id}:${item.ref.unit}:${item.ref.start}:${index}`} className="block break-all"><input type="checkbox" checked={selected.includes(index)} disabled={busy}
      onChange={(event) => { const checked = event.target.checked; setSelected((previous) => checked ? [...previous, index] : previous.filter((value) => value !== index)); setCandidate(null); }} /> [资料{index + 1}] {item.label}</label>)}
    <Button type="button" variant="secondary" disabled={busy || !selected.length || !project || !session || !service || ipc.isMock} onClick={() => void prepare(false)}>预览资料初稿</Button>
    {candidate && <div className="space-y-2 rounded border border-line p-2"><p>将按引用编号顺序追加到普通正文或所选章节，保留已有内容。原文展示为选段，不代表完整原件；正文追加时合并空白。</p>
      {candidate.items.map((item) => <div key={item.number} className="break-all"><p>[资料{item.number}] {item.name} · v{item.version} · {item.id} · SHA-256 {item.sha256} · {item.location} · 单元 {item.unit} · 字符 [{item.start}, {item.end}){item.truncated ? " · 此单元选段不完整" : " · 该次读取未显示单元内截断"}</p>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{item.text}</pre></div>)}
      <Button type="button" variant="secondary" disabled={busy} onClick={() => void prepare(true)}>确认追加资料初稿</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setCandidate(null)}>取消预览</Button></div>}
    {busy && <p role="status">正在复读资料…</p>}{notice && <p role="status">{notice}</p>}
  </details>;
}
