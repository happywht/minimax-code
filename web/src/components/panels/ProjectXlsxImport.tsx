import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { parseOfficeCsv, type CsvTable } from "./officeCsv";

type Extraction = { rows: string[][]; date_columns: string[]; percentage_columns: string[]; currency_columns: string[] };
export function ProjectXlsxImport({ project, session, service, onImport }: { project: string | null; session: string | null; service: string | null; onImport: (text: string, table: CsvTable) => void }): JSX.Element {
  const [items, setItems] = useState<AttachmentOriginal[]>([]), [item, setItem] = useState<AttachmentOriginal | null>(null);
  const [sheets, setSheets] = useState<{ name: string; state: "visible" | "hidden" | "veryHidden" }[]>([]), [sheet, setSheet] = useState("");
  const [extraction, setExtraction] = useState<Extraction | null>(null), [columns, setColumns] = useState<number[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), host = ipc.agentBaseUrl;
  useEffect(() => { epoch.current++; setItems([]); setItem(null); setSheets([]); setSheet(""); setExtraction(null); setColumns([]); setBusy(false); setError(""); }, [project, session, service, host]);
  useEffect(() => { const off = ipc.onSideCar(() => { epoch.current++; setItems([]); setItem(null); setExtraction(null); setBusy(false); setError("连接变化，请重新选择附件。"); }); return () => { epoch.current++; off(); }; }, []);
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const list = async () => {
    if (!project || !session || !service) return;
    const ticket = ++epoch.current; setBusy(true); setError(""); setItems([]); setItem(null); setExtraction(null);
    try { const result = await typedIPC.listAttachments(project); if (!current(ticket)) return;
      if (result.service_id !== service || result.attachments.some((x) => x.project_id !== project || x.service_id !== service)) throw new Error("附件项目或服务不匹配");
      setItems(result.attachments.filter((x) => x.declared_type === "xlsx" && x.recognized_type === "xlsx" && x.size_bytes <= 2097152));
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const select = async (id: string) => {
    const row = items.find((x) => x.id === id); if (!row || !project || !session || !service) return;
    const ticket = ++epoch.current; setItem(null); setSheets([]); setSheet(""); setExtraction(null); setBusy(true); setError("");
    try { const result = await typedIPC.listAttachmentXlsxSheets({ project_id: project, session_id: session, expected_service_id: service, attachment_id: id, expected_sha256: row.sha256 }); if (!current(ticket)) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== id || result.sha256 !== row.sha256) throw new Error("附件身份变化，请重新读取");
      setItem(row); setSheets(result.sheets); setSheet(result.sheets.find((x) => x.state === "visible")?.name ?? "");
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const extract = async () => {
    if (!project || !session || !service || !item || !sheet.trim()) return;
    const ticket = ++epoch.current; setBusy(true); setError(""); setExtraction(null);
    try {
      const result = await typedIPC.extractAttachmentXlsx({ project_id: project, session_id: session, expected_service_id: service, attachment_id: item.id, expected_sha256: item.sha256, sheet_name: sheet });
      if (!current(ticket)) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== item.id || result.sha256 !== item.sha256 || result.sheet_name !== sheet || !result.rows.length) throw new Error("工作表与请求不匹配");
      setExtraction(result); setColumns(result.rows[0].map((_, i) => i));
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const load = () => {
    if (!extraction || !columns.length) return;
    try {
      const csv = extraction.rows.map((row) => columns.map((i) => `"${(row[i] ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
      onImport(csv, parseOfficeCsv(csv)); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return <details className="rounded border border-line p-3"><summary>从项目 XLSX 读取表格</summary>
    <p>选择本服务当前项目的 XLSX 原件（最多 2 MiB），选择可见工作表及列，读取后生成独立新表，不修改原件。只支持完整有界 A1:T1001、最多 5000 格；公式、隐藏行列、合并单元格、图表、筛选、错误值和不明确的数字格式会拒绝。日期变成 ISO 文本；百分比和货币保留底层数值（25% 为 0.25），样式、货币符号和 Excel 排版不保留。</p>
    {!project || !session || !service ? <p>请先选择项目会话并连接服务。</p> : <>
      <Button type="button" variant="secondary" disabled={busy} onClick={() => void list()}>读取项目 XLSX 附件</Button>
      <label>附件 <select aria-label="项目 XLSX 附件" value={item?.id ?? ""} disabled={busy} onChange={(e) => void select(e.target.value)}><option value="">选择附件</option>{items.map((x) => <option key={x.id} value={x.id}>{x.original_name} · v{x.version}</option>)}</select></label>
      {item && <><p>工作表列表来自完整元数据；隐藏表不可读取，可见表仍可能含不支持的内容。</p><label>可见工作表 <select aria-label="XLSX 可见工作表" value={sheets.some((x) => x.name === sheet && x.state === "visible") ? sheet : ""} onChange={(e) => { setSheet(e.target.value); setExtraction(null); }}><option value="">选择或手动填写</option>{sheets.map((entry) => <option key={entry.name} value={entry.name} disabled={entry.state !== "visible"}>{entry.name}{entry.state === "visible" ? "" : ` · ${entry.state}`}</option>)}</select></label>
        <Input aria-label="XLSX 工作表名称" value={sheet} maxLength={31} placeholder="准确的可见工作表名称" onChange={(e) => { setSheet(e.target.value); setExtraction(null); }} />
        <Button type="button" variant="secondary" disabled={busy || !sheet.trim()} onClick={() => void extract()}>读取完整工作表</Button></>}
      {busy && <p role="status">正在读取…</p>}{error && <p role="alert">{error}</p>}
      {extraction && <div className="space-y-1"><p>已读取 {extraction.rows.length - 1} 行；选择要导入的列。默认列类型为文本，可在表格中调整。</p>
        {extraction.rows[0].map((name, i) => <label key={i} className="block"><input type="checkbox" checked={columns.includes(i)} onChange={(e) => setColumns(e.target.checked ? [...columns, i].sort((a, b) => a - b) : columns.filter((x) => x !== i))} /> {name || `第 ${i + 1} 列（空表头）`}
          {extraction.date_columns.includes(name) ? " · 日期转 ISO 文本" : ""}{extraction.percentage_columns.includes(name) ? " · 百分比底层值" : ""}{extraction.currency_columns.includes(name) ? " · 货币底层值" : ""}</label>)}
        <Button type="button" disabled={!columns.length} onClick={load}>载入所选列到表格草稿</Button></div>}
    </>}
  </details>;
}
