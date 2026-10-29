import type { OfficeTableSpec } from "../../types/ipc";

type Kind = OfficeTableSpec["columns"][number]["kind"];
export type CsvTable = { headers: string[]; rows: string[][] };

export function parseOfficeCsv(input: string): CsvTable {
  if (new TextEncoder().encode(input).length > 1048576) throw new Error("CSV 超过 1 MiB");
  const source = input.replace(/^\uFEFF/, "");
  if (!source) throw new Error("请输入带表头的 CSV");
  const rows: string[][] = [];
  let row: string[] = [], field = "", quoted = false, closed = false, started = false;
  const endField = () => { row.push(field); field = ""; closed = false; started = false; };
  const endRow = () => { endField(); rows.push(row); row = []; if (rows.length > 1001) throw new Error("CSV 最多 1000 数据行"); };
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') { field += '"'; i++; }
      else if (char === '"') { quoted = false; closed = true; }
      else field += char;
    } else if (char === ",") endField();
    else if (char === "\r" || char === "\n") { endRow(); if (char === "\r" && source[i + 1] === "\n") i++; }
    else if (char === '"' && !started && !closed) { quoted = true; started = true; }
    else if (char === '"' || closed) throw new Error(`CSV 第 ${rows.length + 1} 行引号格式无效`);
    else { field += char; started = true; }
  }
  if (quoted) throw new Error("CSV 引号未闭合");
  if (row.length || started || closed || !/[\r\n]$/.test(source)) endRow();
  const headers = rows.shift()!;
  if (headers.length > 20 || headers.some((name) => !name.trim() || [...name].length > 80)) throw new Error("表头须有 1–20 个非空列名，每个最多 80 字符");
  if ((rows.length + 1) * headers.length > 5000) throw new Error("含表头最多 5000 格");
  for (const [index, values] of rows.entries()) {
    if (values.length !== headers.length) throw new Error(`CSV 第 ${index + 2} 行列数与表头不一致`);
    if (values.some((value) => [...value].length > 4096)) throw new Error(`CSV 第 ${index + 2} 行的单元格超过 4096 字符`);
  }
  return { headers, rows };
}

// Compare decimal values without losing digits through JavaScript Number.
function decimalParts(input: string): string | null {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(input);
  if (!match || (match[4] && Math.abs(Number(match[4])) > 1000)) return null;
  let digits = (match[2] + (match[3] || "")).replace(/^0+/, "");
  if (!digits) return match[1] === "-" ? null : "0";
  let exponent = Number(match[4] || 0) - (match[3] || "").length;
  while (digits.endsWith("0")) { digits = digits.slice(0, -1); exponent++; }
  if (digits.length > 15) return null;
  return `${match[1] === "-" ? "-" : ""}${digits}e${exponent}`;
}

export function csvToOfficeSpec(table: CsvTable, kinds: Kind[]): OfficeTableSpec {
  if (new TextEncoder().encode(JSON.stringify(table)).length > 1048576) throw new Error("编辑后的表格超过 1 MiB");
  const rows = table.rows.map((row, index) => row.map((value, column) => {
    if ([...value].length > 4096) throw new Error(`表格第 ${index + 2} 行「${table.headers[column]}」超过 4096 字符`);
    if (value === "") return null;
    if (kinds[column] === "text") return value;
    const clean = value.trim();
    if (kinds[column] === "boolean") {
      if (/^true$/i.test(clean)) return true;
      if (/^false$/i.test(clean)) return false;
    } else {
      const normalized = decimalParts(clean), number = Number(clean);
      if (normalized !== null && Number.isFinite(number) && Math.abs(number) <= 999999999999999
          && (number === 0 || Math.abs(number) >= 1e-12) && decimalParts(String(number)) === normalized) return number;
    }
    throw new Error(`表格第 ${index + 2} 行「${table.headers[column]}」不是有效的${kinds[column] === "boolean" ? "布尔值（true/false）" : "数字"}`);
  }));
  return { sheet_name: "清单", columns: table.headers.map((name, index) => ({ name, kind: kinds[index] })), rows };
}
