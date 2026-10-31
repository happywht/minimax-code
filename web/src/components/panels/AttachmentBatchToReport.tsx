import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentOriginal, DocumentExcerptRef } from "../../types/ipc";
import { Button } from "../../ui";

export type BatchDocumentExcerpt = { ref: DocumentExcerptRef; label: string; text: string };
export function AttachmentBatchToReport({ project, session, service, host, items, draftVersion, onApply }: {
  project: string | null; session: string | null; service: string | null; host: string; items: AttachmentOriginal[];
  draftVersion: string; onApply: (items: BatchDocumentExcerpt[]) => boolean;
}): JSX.Element {
  const [selected, setSelected] = useState<AttachmentOriginal[]>([]), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), draft = useRef({ value: draftVersion, revision: 0 }), callback = useRef(onApply);
  callback.current = onApply;
  if (draft.current.value !== draftVersion) draft.current = { value: draftVersion, revision: draft.current.revision + 1 };
  useEffect(() => { epoch.current++; setSelected([]); setBusy(false); setNotice(""); return () => { epoch.current++; }; }, [project, session, service, host]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setSelected([]); setBusy(false); setNotice("连接变化，请重新选择资料。"); }), []);
  const eligible = items.filter((row) => (row.declared_type === row.recognized_type && ["pdf", "docx"].includes(row.declared_type)) ||
    (["txt", "csv"].includes(row.declared_type) && row.recognized_type === "utf8_text"));
  const add = async () => {
    if (!project || !session || !service || busy || !selected.length) return;
    const ticket = ++epoch.current, revision = draft.current.revision, chosen = [...selected];
    const current = () => {
      const active = useSessionStore.getState(), store = useAttachmentStore.getState();
      return ticket === epoch.current && draft.current.revision === revision && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && store.projectId === project && store.host === host && store.serviceId === service;
    };
    setBusy(true); setNotice("");
    try {
      const excerpts: BatchDocumentExcerpt[] = [];
      for (const row of chosen) {
        if (!current()) { if (ticket === epoch.current) setNotice("报告或归属已变化，未添加任何内容。"); return; }
        if (row.project_id !== project || row.service_id !== service) throw new Error("资料归属已变化");
        const result = await typedIPC.readAttachmentDocument({ project_id: project, session_id: session, expected_service_id: service,
          attachment_id: row.id, expected_sha256: row.sha256, unit: 1, offset: 0 });
        if (!current()) { if (ticket === epoch.current) setNotice("报告或归属已变化，未添加任何内容。"); return; }
        const expectedKind = row.recognized_type === "utf8_text" ? row.declared_type : row.recognized_type;
        if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== row.id ||
            result.sha256 !== row.sha256 || result.kind !== expectedKind || result.unit !== 1 || result.start !== 0 ||
            result.end !== Array.from(result.text).length || result.end > 4000) throw new Error("资料身份或读取位置不匹配");
        const text = Array.from(result.text).slice(0, 600).join("");
        if (!text.trim()) throw new Error(`${row.original_name} 的首个可读取位置没有文字；请手动选择其他位置`);
        const end = Array.from(text).length;
        excerpts.push({ ref: { attachment_id: row.id, expected_sha256: row.sha256, unit: 1, start: 0, end },
          label: `${row.original_name} · ${result.location} · 字符 0–${end}`, text });
      }
      if (!current()) { if (ticket === epoch.current) setNotice("报告或归属已变化，未添加任何内容。"); return; }
      if (!callback.current(excerpts)) throw new Error("未添加；请检查引用数量、目标章节及报告长度限制");
      setSelected([]); setNotice("已将首段摘录和资料引用加入报告草稿；请核对编号、范围和事实。");
    } catch (reason) { if (current()) setNotice(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <details className="space-y-2 rounded border border-line p-3"><summary>批量整理附件首段到报告</summary>
    <p>最多选择 10 份附件；每份只引用首个可读单元的前 600 字符，未经核验。PDF 不做 OCR，DOCX 仅读正文和表格段落。</p>
    <div className="max-h-48 overflow-auto">{eligible.map((row) => <label key={row.id} className="block break-all"><input type="checkbox" checked={selected.some((item) => item.id === row.id)}
      disabled={busy || (selected.length >= 10 && !selected.some((item) => item.id === row.id))} onChange={(event) => { const checked = event.target.checked;
        setSelected((previous) => checked ? [...previous, row] : previous.filter((item) => item.id !== row.id)); }} /> {row.original_name} · v{row.version} · {row.id}</label>)}</div>
    <p>已选择 {selected.length} 份：{selected.map((row) => row.original_name).join(" → ") || "无"}。若有资料首段为空或任何读取失败，整个批次不追加；可改用单份分段读取。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !session || !service || ipc.isMock || !selected.length} onClick={() => void add()}>核对并批量加入报告</Button>
    {busy && <p role="status">正在逐份读取原件…</p>}{notice && <p role="status">{notice}</p>}
  </details>;
}
