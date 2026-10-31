import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentGetResult, AttachmentDocumentResult, DocumentExcerptRef, PageExcerptRange, ReportSource, ResearchSource } from "../../types/ipc";
import { Button } from "../../ui";
import { readEditableSections } from "./ReportSectionsEditor";

type Draft = { filename: string; title: string; body: string; structured: boolean; sections: string; sources: ReportSource[];
  researchSources: ResearchSource[]; researchRanges: PageExcerptRange[]; documentCitations: { ref: DocumentExcerptRef; label: string }[] };

/** Text export is a separate, local delivery; it does not register a report. */
export function ReportTextExport({ project, session, service, host, draft, disabled }: {
  project: string | null; session: string | null; service: string | null; host: string; draft: Draft; disabled: boolean;
}): JSX.Element {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), snapshot = JSON.stringify(draft), versionKey = JSON.stringify([snapshot, disabled]), version = useRef({ value: versionKey, revision: 0 });
  if (version.current.value !== versionKey) version.current = { value: versionKey, revision: version.current.revision + 1 };
  useEffect(() => { epoch.current++; setBusy(false); setNotice(""); return () => { epoch.current++; }; }, [project, session, service, host]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setBusy(false); setNotice("连接变化，请重新导出。"); }), []);
  const exportText = async () => {
    if (!project || !session || !service || busy || disabled) return;
    const ticket = ++epoch.current, expectedRevision = version.current.revision;
    const value: Draft = JSON.parse(snapshot);
    const current = () => {
      const active = useSessionStore.getState(), store = useAttachmentStore.getState();
      return ticket === epoch.current && version.current.revision === expectedRevision && ipc.agentBaseUrl === host &&
        active.currentProjectId === project && active.currentSessionId === session && store.projectId === project && store.host === host && store.serviceId === service;
    };
    const check = async () => {
      if (!current()) throw new Error("报告或归属已变化，未导出");
      const [identity, bound] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
      if (!current() || identity.service_id !== service || bound.session.id !== session || bound.session.project_id !== project)
        throw new Error("报告或归属已变化，未导出");
    };
    setBusy(true); setNotice("");
    try {
      if (!value.title.trim()) throw new Error("请填写报告标题");
      if (value.sources.some((source) => !source.title.trim() || !source.url.trim())) throw new Error("请填写手工来源标题和网址");
      const sections = value.structured ? readEditableSections(value.sections) : null;
      if (value.structured && !sections) throw new Error("章节 JSON 无法无损读取，请先修正");
      if (!value.structured && !value.body.trim()) throw new Error("请填写报告正文");
      await check();
      const lines = [value.title, "", "（可编辑文本导出；内容、引用及版式未经核验）", ""];
      if (sections) {
        for (const section of sections) {
          lines.push(section.heading, "");
          for (const block of section.blocks) {
            if (block.kind === "paragraph") lines.push(block.text, "");
            else {
              lines.push(`表格（每行是 JSON 字符串数组；${block.repeat_header ? "首行设为重复表头" : "未设置重复表头"}）：`);
              block.rows.forEach((row) => lines.push(JSON.stringify(row))); lines.push("");
            }
          }
        }
      } else lines.push(value.body, "");
      if (value.documentCitations.length) lines.push("项目附件摘录（仅所选范围，未经核验）", "");
      for (const [index, item] of value.documentCitations.entries()) {
        const ref = item.ref;
        const metadata: AttachmentGetResult = await typedIPC.getAttachment(project, ref.attachment_id);
        const result: AttachmentDocumentResult = await typedIPC.readAttachmentDocument({ project_id: project,
          session_id: session, expected_service_id: service, attachment_id: ref.attachment_id, expected_sha256: ref.expected_sha256, unit: ref.unit, offset: ref.start });
        if (!current()) throw new Error("报告或归属已变化，未导出");
        const row = metadata.attachment;
        if (row.project_id !== project || row.service_id !== service || row.id !== ref.attachment_id || row.sha256 !== ref.expected_sha256 ||
            result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== ref.attachment_id ||
            result.sha256 !== ref.expected_sha256 || result.unit !== ref.unit || result.start !== ref.start || result.end < ref.end ||
            row.declared_type !== result.kind || !(row.recognized_type === result.kind || (row.recognized_type === "utf8_text" && (result.kind === "txt" || result.kind === "csv"))) ||
            ref.end <= ref.start || ref.end - ref.start > 1000 || Array.from(result.text).length < ref.end - ref.start)
          throw new Error("附件身份或选段已变化，未导出");
        const text = Array.from(result.text).slice(0, ref.end - ref.start).join("");
        lines.push(`[资料${index + 1}] ${row.original_name} · ${row.id} · SHA-256 ${row.sha256}`,
          `位置：${result.location}，单元 ${result.unit}/${result.unit_total}，字符 [${ref.start}, ${ref.end})；仅所选摘录。`,
          `读取范围：${result.kind === "pdf" ? "PDF 物理页已有文字，不做 OCR 或版式还原" : result.kind === "docx" ? "DOCX 正文及表格段落，不含其他部件或 OCR" : "UTF-8 原文；CSV 不解析表格或计算公式"}。`, `摘录：${text}`, "");
      }
      if (value.sources.length || value.researchSources.length) lines.push("参考来源（未经事实核验）", "");
      for (const [index, source] of value.sources.entries()) lines.push(`[${index + 1}] ${source.title}（用户填写）`, `网址：${source.url}`,
        ...(source.accessed_at?.trim() ? [`访问时间（用户填写）：${source.accessed_at}`] : []), "");
      for (const [index, selected] of value.researchSources.entries()) {
        const response = await typedIPC.getResearchSource({ project_id: project, session_id: session, expected_service_id: service, page_source_id: selected.page_source_id });
        if (!current()) throw new Error("报告或归属已变化，未导出");
        if (response.project_id !== project || response.session_id !== session || response.service_id !== service || response.source.page_source_id !== selected.page_source_id)
          throw new Error("研究来源归属已变化，未导出");
        const source = response.source, excerpt = Array.from(source.excerpt ?? ""), range = value.researchRanges.find((item) => item.page_source_id === source.page_source_id);
        if (range && (range.start < 0 || range.end > excerpt.length || range.end <= range.start || range.end - range.start > 1000)) throw new Error("研究来源选段已变化，未导出");
        const start = range?.start ?? 0, end = range?.end ?? Math.min(1000, excerpt.length);
        lines.push(`[${value.sources.length + index + 1}] ${source.title || "（页面未提供标题）"}${source.title_truncated ? "（标题已截断）" : ""}`,
          `网址：${source.url}`, `实际访问时间：${source.accessed_at}`, `来源 ID：${source.page_source_id}`,
          `保存摘录字符 [${start}, ${end})${source.excerpt_truncated || source.truncated || start > 0 || end < excerpt.length ? "（已截断，不代表全文）" : ""}：${excerpt.slice(start, end).join("") || "（无提取文本）"}`, "");
      }
      await check();
      const text = lines.join("\n");
      if (new TextEncoder().encode(text).length > 1024 * 1024) throw new Error("文本超过 1 MiB，未导出");
      const blobUrl = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const anchor = document.createElement("a"); anchor.href = blobUrl;
      anchor.download = `${value.filename.replace(/\.(docx|pdf)$/i, "").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_") || "报告"}.txt`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      setNotice("已交给浏览器下载文本；未登记附件，内容和版式仍需核对。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-line p-2"><p>导出可编辑 TXT，包含正文和表格数据；不保留排版或登记附件。请核对内容、引用和事实。</p>
    <Button type="button" variant="secondary" disabled={disabled || busy || !project || !session || !service || ipc.isMock} onClick={() => void exportText()}>导出可编辑报告 TXT</Button>
    {busy && <p role="status">正在核对来源并准备文本…</p>}{notice && <p role="status">{notice}</p>}</div>;
}
