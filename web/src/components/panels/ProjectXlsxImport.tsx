import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { parseOfficeCsv, type CsvTable } from "./officeCsv";

type Extraction = { rows: string[][]; date_columns: string[]; percentage_columns: string[]; currency_columns: string[]; formula_cells?: string[] };
export function ProjectXlsxImport({ project, session, service, disabled = false, onImport }: { project: string | null; session: string | null; service: string | null; disabled?: boolean; onImport: (text: string, table: CsvTable, cacheInfo?: string) => void }): JSX.Element {
  const [items, setItems] = useState<AttachmentOriginal[]>([]), [item, setItem] = useState<AttachmentOriginal | null>(null);
  const [sheets, setSheets] = useState<{ name: string; state: "visible" | "hidden" | "veryHidden" }[]>([]), [sheet, setSheet] = useState("");
  const [extraction, setExtraction] = useState<Extraction | null>(null), [columns, setColumns] = useState<number[]>([]);
  const [firstRow, setFirstRow] = useState("1"), [lastRow, setLastRow] = useState("0");
  const [query, setQuery] = useState(""), [applied, setApplied] = useState(""), [cursor, setCursor] = useState<string | null>(null), [loaded, setLoaded] = useState(false);
  const [useCache, setUseCache] = useState(false), [ackCache, setAckCache] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), host = ipc.agentBaseUrl, blocked = useRef(disabled); blocked.current = disabled;
  useEffect(() => { epoch.current++; setItems([]); setItem(null); setSheets([]); setSheet(""); setExtraction(null); setColumns([]); setQuery(""); setApplied(""); setCursor(null); setLoaded(false); setFirstRow("1"); setLastRow("0"); setUseCache(false); setAckCache(false); setBusy(false); setError(""); }, [project, session, service, host]);
  useEffect(() => { const off = ipc.onSideCar(() => { epoch.current++; setItems([]); setItem(null); setExtraction(null); setCursor(null); setLoaded(false); setBusy(false); setError("连接变化，请重新选择附件。"); }); return () => { epoch.current++; off(); }; }, []);
  const current = (ticket: number) => ticket === epoch.current && !blocked.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const list = async (more = false) => {
    if (!project || !session || !service || disabled) return;
    const ticket = ++epoch.current, term = more ? applied : query.trim(), next = more ? cursor : null;
    setBusy(true); setError("");
    if (!more) { setItems([]); setItem(null); setExtraction(null); }
    try { const result = await typedIPC.listAttachments(project, next ?? undefined, term || undefined); if (!current(ticket)) return;
      if (result.service_id !== service || result.attachments.length > 200 || result.attachments.some((x) => x.project_id !== project || x.service_id !== service) ||
          (result.next_cursor != null && (result.next_cursor === next || (term ? !result.next_cursor.startsWith("q_") : !/^att_[a-f0-9]{32}$/.test(result.next_cursor))))) throw new Error("附件项目、服务或游标不匹配");
      const found = result.attachments.filter((x) => x.declared_type === "xlsx" && x.recognized_type === "xlsx" && x.size_bytes <= 2097152);
      setItems((old) => more ? [...old, ...found.filter((row) => !old.some((item) => item.id === row.id))] : found);
      setCursor(result.next_cursor ?? null); setApplied(term); setLoaded(true);
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const select = async (id: string) => {
    if (!id) { setItem(null); setSheets([]); setSheet(""); setExtraction(null); return; }
    const row = items.find((x) => x.id === id); if (!row || !project || !session || !service || disabled) return;
    const ticket = ++epoch.current; setItem(null); setSheets([]); setSheet(""); setExtraction(null); setBusy(true); setError("");
    try { const result = await typedIPC.listAttachmentXlsxSheets({ project_id: project, session_id: session, expected_service_id: service, attachment_id: id, expected_sha256: row.sha256 }); if (!current(ticket)) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== id || result.sha256 !== row.sha256) throw new Error("附件身份变化，请重新读取");
      setItem(row); setSheets(result.sheets); setSheet(result.sheets.find((x) => x.state === "visible")?.name ?? "");
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const extract = async () => {
    if (!project || !session || !service || !item || !sheet.trim() || disabled) return;
    const ticket = ++epoch.current; setBusy(true); setError(""); setExtraction(null);
    try {
      const result = await typedIPC.extractAttachmentXlsx({ project_id: project, session_id: session, expected_service_id: service, attachment_id: item.id, expected_sha256: item.sha256, sheet_name: sheet, ...(useCache ? { use_formula_cache: true } : {}) });
      if (!current(ticket)) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== item.id || result.sha256 !== item.sha256 || result.sheet_name !== sheet || !result.rows.length || result.rows.length > 1001 ||
          !result.rows[0].length || result.rows[0].length > 20 || result.rows.some((row) => row.length !== result.rows[0].length || row.some((cell) => typeof cell !== "string")) ||
          (useCache ? result.use_formula_cache !== true || !Array.isArray(result.formula_cells) : result.use_formula_cache === true)) throw new Error("工作表与请求不匹配");
      setExtraction(result); setColumns(result.rows[0].map((_, i) => i)); setFirstRow("1"); setLastRow(String(result.rows.length - 1)); setAckCache(false);
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const count = extraction ? extraction.rows.length - 1 : 0;
  const first = Number(firstRow), last = Number(lastRow);
  const validRange = count === 0 || (/^\d+$/.test(firstRow) && /^\d+$/.test(lastRow) && Number.isSafeInteger(first) && Number.isSafeInteger(last) && first >= 1 && last >= first && last <= count);
  const chosenRows = extraction && validRange ? [extraction.rows[0], ...(count ? extraction.rows.slice(first, last + 1) : [])] : null;
  const selectedFormulaCells = (extraction?.formula_cells ?? []).filter((coordinate) => {
    const match = /^([A-Z]+)([1-9][0-9]*)$/.exec(coordinate);
    if (!match) return false;
    const column = [...match[1]].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0) - 1, row = Number(match[2]);
    return columns.includes(column) && (row === 1 || (count > 0 && validRange && row >= first + 1 && row <= last + 1));
  });
  const load = () => {
    if (!chosenRows || !columns.length || disabled || (selectedFormulaCells.length && !ackCache)) return;
    try {
      const csv = chosenRows.map((row) => columns.map((i) => `"${row[i].replace(/"/g, '""')}"`).join(",")).join("\n");
      const cacheInfo = selectedFormulaCells.length && item ? `来源 XLSX ${item.original_name} · ${item.id} · SHA-256 ${item.sha256} · 工作表 ${sheet}；导入了 ${selectedFormulaCells.length} 个公式单元格的未核验存储缓存（可能过期），位置：${selectedFormulaCells.slice(0, 50).join("、")}${selectedFormulaCells.length > 50 ? "（仅列前 50 个）" : ""}；未执行或重算公式。` : undefined;
      onImport(csv, parseOfficeCsv(csv), cacheInfo); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return <details className="rounded border border-line p-3"><summary>从项目 XLSX 读取表格</summary>
    <p>选择本服务当前项目的 XLSX 原件（最多 2 MiB），读取受支持的完整可见工作表，再选择数据行和列载入草稿；原件不修改。默认拒绝公式，可显式选择仅读取公式的已存储缓存；不会执行或重算公式，缓存可能缺失或过期。只支持完整有界 A1:T1001、最多 5000 格；隐藏行列、合并单元格、图表、筛选、错误值和不明确的数字格式会拒绝。日期变成 ISO 文本；百分比和货币保留底层数值（25% 为 0.25），样式、货币符号和 Excel 排版不保留。缓存导入功能尚未运行验证。</p>
    {!project || !session || !service ? <p>请先选择项目会话并连接服务。</p> : <>
      <div className="flex gap-2"><Input aria-label="搜索项目 XLSX 附件" maxLength={240} value={query} disabled={busy || disabled} onChange={(event) => setQuery(event.target.value)} placeholder="按名称搜索" /><Button type="button" variant="secondary" disabled={busy || disabled} onClick={() => void list()}>读取项目 XLSX 附件</Button></div>
      {loaded && !items.length && <p>{cursor ? "当前已加载页没有受支持的 XLSX，可继续加载。" : "没有匹配的受支持 XLSX。"}</p>}
      {cursor && <Button type="button" variant="secondary" disabled={busy || disabled} onClick={() => void list(true)}>加载更早 XLSX 附件</Button>}
      <label>附件 <select aria-label="项目 XLSX 附件" value={item?.id ?? ""} disabled={busy || disabled} onChange={(e) => void select(e.target.value)}><option value="">选择附件</option>{items.map((x) => <option key={x.id} value={x.id}>{x.original_name} · v{x.version}</option>)}</select></label>
      {item && <><p className="break-all">来源：{item.original_name} · v{item.version} · {item.id} · SHA-256 {item.sha256}</p><p>工作表列表来自完整元数据；隐藏表不可读取，可见表仍可能含不支持的内容。</p><label>可见工作表 <select aria-label="XLSX 可见工作表" disabled={busy || disabled} value={sheets.some((x) => x.name === sheet && x.state === "visible") ? sheet : ""} onChange={(e) => { setSheet(e.target.value); setExtraction(null); }}><option value="">选择或手动填写</option>{sheets.map((entry) => <option key={entry.name} value={entry.name} disabled={entry.state !== "visible"}>{entry.name}{entry.state === "visible" ? "" : ` · ${entry.state}`}</option>)}</select></label>
        <Input aria-label="XLSX 工作表名称" value={sheet} disabled={busy || disabled} maxLength={31} placeholder="准确的可见工作表名称" onChange={(e) => { setSheet(e.target.value); setExtraction(null); }} />
        <label className="block"><input type="checkbox" checked={useCache} disabled={busy || disabled} onChange={(event) => { setUseCache(event.target.checked); setExtraction(null); setAckCache(false); }} /> 明确选择仅使用公式已存储缓存值（可能过期、未经核验）</label>
        <Button type="button" variant="secondary" disabled={busy || disabled || !sheet.trim()} onClick={() => void extract()}>读取完整工作表</Button></>}
      {busy && <p role="status">正在读取…</p>}{error && <p role="alert">{error}</p>}
      {extraction && <div className="space-y-1"><p>已读取完整受支持工作表的 {count} 个数据行。选择列和连续行范围；表头始终保留，默认列类型为文本。</p>
        {useCache && <p role="status">工作表检测到 {extraction.formula_cells?.length ?? 0} 个公式缓存单元格；选区含 {selectedFormulaCells.length} 个。只使用存储缓存，未计算、未验证，可能过期；缺失或空缓存、错误及不支持的公式类型会拒绝整个工作表。</p>}
        {extraction.rows[0].map((name, i) => <label key={i} className="block"><input type="checkbox" checked={columns.includes(i)} onChange={(e) => { setColumns(e.target.checked ? [...columns, i].sort((a, b) => a - b) : columns.filter((x) => x !== i)); setAckCache(false); }} /> {name || `第 ${i + 1} 列（空表头）`}
          {extraction.date_columns.includes(name) ? " · 日期转 ISO 文本" : ""}{extraction.percentage_columns.includes(name) ? " · 百分比底层值" : ""}{extraction.currency_columns.includes(name) ? " · 货币底层值" : ""}</label>)}
        {count > 0 ? <div className="flex flex-wrap gap-2"><label>起始数据行（从 1 开始，不含表头）<Input aria-label="XLSX 起始数据行" type="number" min={1} max={count} value={firstRow} onChange={(event) => { setFirstRow(event.target.value); setAckCache(false); }} /></label>
          <label>结束数据行（包含）<Input aria-label="XLSX 结束数据行" type="number" min={1} max={count} value={lastRow} onChange={(event) => { setLastRow(event.target.value); setAckCache(false); }} /></label></div> : <p>工作表只有表头。</p>}
        {!validRange && <p role="alert">请选择有效的数据行范围。</p>}
        {chosenRows && columns.length > 0 && <details><summary>预览所选表头和前 5 个数据行</summary><pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words">{chosenRows.slice(0, 6).map((row) => JSON.stringify(columns.map((column) => row[column]))).join("\n")}</pre></details>}
        {selectedFormulaCells.length > 0 && <div><p className="break-all">选区中公式缓存位置：{selectedFormulaCells.slice(0, 50).join("、")}{selectedFormulaCells.length > 50 ? "（仅显示前 50 个）" : ""}</p>
          <label><input type="checkbox" checked={ackCache} onChange={(event) => setAckCache(event.target.checked)} /> 我了解这些值是可能过期的缓存，未经重算或业务核验</label></div>}
        <p>选区按当前工作表行顺序，不反映源文件版式；需将附件身份、工作表和所选范围写入处理说明，业务内容未核验。</p>
        <Button type="button" disabled={busy || disabled || !columns.length || !validRange || (selectedFormulaCells.length > 0 && !ackCache)} onClick={load}>载入所选列到表格草稿</Button></div>}
    </>}
  </details>;
}
