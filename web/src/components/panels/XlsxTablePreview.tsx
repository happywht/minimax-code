import { useMemo, useState } from "react";
import type { AttachmentOriginal } from "../../types/ipc";

type Cell = { coordinate: string; row: number; column: number; kind: string; raw?: string;
  formula?: string; cacheState?: "stored" | "missing" | "unknown"; cachedRaw?: string | null };
type Sheet = { index: number; name: string; state: string; epoch: string; cells: Map<string, Cell>; rows: number; columns: number };

/** Parse only the backend's bounded JSONL projection, never workbook bytes. */
export function parseXlsxProjection(text: string): Sheet[] {
  if (new TextEncoder().encode(text).length > 65536) throw new Error("预览超过上限");
  const lines = text.split("\n");
  if (lines.at(-1) === "") lines.pop();
  if (lines.length > 2000) throw new Error("预览超过行数上限");
  const sheets: Sheet[] = [];
  let total = 0;
  for (const line of lines) {
    const record: unknown = JSON.parse(line);
    if (!record || typeof record !== "object" || Array.isArray(record)) throw new Error("解析记录格式不支持");
    const item = record as Record<string, unknown>;
    if ("sheet" in item) {
      if (item.sheet !== sheets.length + 1 || typeof item.name !== "string" || !item.name || item.name.length > 65536
          || !["visible", "hidden", "veryHidden"].includes(String(item.state)) || !["1900", "1904"].includes(String(item.epoch))
          || item.window !== "A1:T100" || item.values !== "stored_raw" || sheets.length >= 32) throw new Error("工作表记录格式不支持");
      sheets.push({ index: item.sheet as number, name: item.name, state: String(item.state), epoch: String(item.epoch), cells: new Map(), rows: 0, columns: 0 });
      continue;
    }
    const sheet = sheets.at(-1);
    const match = typeof item.cell === "string" ? /^([A-T])([1-9]|[1-9][0-9]|100)$/.exec(item.cell) : null;
    if (!sheet || !match || sheet.cells.has(item.cell as string) || ++total > 1500) throw new Error("单元格记录格式不支持");
    const row = Number(match[2]), column = match[1].charCodeAt(0) - 64;
    const cell: Cell = { coordinate: item.cell as string, row, column, kind: String(item.kind) };
    if (item.kind === "formula") {
      if (typeof item.formula !== "string" || !["stored", "missing", "unknown"].includes(String(item.cached_state))
          || typeof item.cached_present !== "boolean" || item.cache_verified !== false || item.recalculated !== false
          || !(item.cached_raw === null || typeof item.cached_raw === "string")
          || (item.cached_state === "missing" && (item.cached_present || item.cached_raw !== null))
          || (item.cached_state === "unknown" && (!item.cached_present || item.cached_raw !== ""))
          || (item.cached_state === "stored" && (!item.cached_present || typeof item.cached_raw !== "string"))) throw new Error("公式记录格式不支持");
      cell.formula = item.formula; cell.cacheState = item.cached_state as Cell["cacheState"]; cell.cachedRaw = item.cached_raw as string | null;
    } else {
      if (!["string", "number", "boolean", "error", "iso_date"].includes(String(item.kind)) || typeof item.raw !== "string")
        throw new Error("单元格值格式不支持");
      cell.raw = item.raw;
    }
    sheet.cells.set(cell.coordinate, cell);
    sheet.rows = Math.max(sheet.rows, row); sheet.columns = Math.max(sheet.columns, column);
  }
  return sheets;
}

export function XlsxTablePreview({ attachment, preview }: { attachment: AttachmentOriginal; preview: string }): JSX.Element | null {
  const [sheetIndex, setSheetIndex] = useState(0);
  const parsed = useMemo(() => {
    if (attachment.recognized_type !== "xlsx" || attachment.declared_type !== "xlsx") return { sheets: [] as Sheet[], error: false };
    try {
      const sheets = parseXlsxProjection(preview);
      if (!sheets.length && attachment.parser_status !== "empty") throw new Error("missing worksheet");
      return { sheets, error: false };
    }
    catch { return { sheets: [] as Sheet[], error: true }; }
  }, [preview, attachment.recognized_type, attachment.declared_type, attachment.parser_status]);
  if (attachment.recognized_type !== "xlsx" || attachment.declared_type !== "xlsx"
      || attachment.parser_version !== 2 || attachment.parser_scope !== "inert_text"
      || !["parsed", "truncated", "empty"].includes(attachment.parser_status)) return null;
  const sheet = parsed.sheets[sheetIndex] ?? parsed.sheets[0];
  return <section className="space-y-2 rounded border border-line p-2 text-xs" aria-label="XLSX 表格预览">
    <p>仅显示后端解析的存储记录，最多 A1:T100、1500 格和 64 KiB；「—」表示未显示，不代表单元格为空。格式、合并单元格、图表及布局不还原。</p>
    <p>数字按原始存储值显示，不按日期、货币或百分比格式推断；ISO 日期保留原文。公式不执行，缓存可能缺失或过期，均未核验。</p>
    {attachment.preview_truncated && <p role="status">预览已截断，未显示的区域及工作表不可推断；可下载完整原件。</p>}
    {parsed.error ? <p role="alert">结构化预览无法读取；可查看原始解析记录或下载原件。</p> : !sheet ?
      <p role="status">预览范围没有可显示的非空记录；不代表整个文件为空。</p> : <>
      <label>工作表 <select aria-label="选择工作表" value={sheet.index} onChange={(event) => setSheetIndex(Number(event.target.value) - 1)} className="rounded border border-line bg-surface-1 p-1">
        {parsed.sheets.map((entry) => <option key={entry.index} value={entry.index}>{entry.name}{entry.state !== "visible" ? "（隐藏）" : ""}</option>)}
      </select></label>
      <p>工作表状态：{sheet.state}；日期系统：{sheet.epoch}（不转换序列值）。</p>
      {sheet.rows === 0 ? <p>此预览中没有该表的单元格记录。</p> : <div className="max-h-96 overflow-auto">
        <table className="border-collapse text-left" aria-label={`工作表 ${sheet.name}`}><thead><tr><th className="border border-line p-1">行</th>
          {Array.from({ length: sheet.columns }, (_, column) => <th key={column} className="border border-line p-1">{String.fromCharCode(65 + column)}</th>)}</tr></thead>
          <tbody>{Array.from({ length: sheet.rows }, (_, r) => <tr key={r}><th className="border border-line p-1">{r + 1}</th>
            {Array.from({ length: sheet.columns }, (_, c) => {
              const coordinate = `${String.fromCharCode(65 + c)}${r + 1}`, cell = sheet.cells.get(coordinate);
              return <td key={coordinate} aria-label={coordinate} className="min-w-24 max-w-80 whitespace-pre-wrap break-all border border-line p-1 align-top">
                {!cell ? "—" : cell.kind === "formula" ? <><span>公式：={cell.formula}</span><br />
                  <span>缓存（未核验）：{cell.cacheState === "missing" ? "缺失" : cell.cacheState === "unknown" ? "不确定" : cell.cachedRaw === "" ? "空字符串" : cell.cachedRaw}</span></>
                  : <><span>{cell.raw}</span><br /><span className="text-ink-2">{({ string: "文本", number: "数字原值", boolean: "布尔原值", error: "错误值", iso_date: "ISO 日期原文" } as Record<string, string>)[cell.kind]}</span></>}
              </td>;
            })}</tr>)}</tbody></table></div>}
    </>}
  </section>;
}
