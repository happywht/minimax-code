import type { OfficeTableSpec } from "../../types/ipc";

type Kind = OfficeTableSpec["columns"][number]["kind"];
export type CsvTable = { headers: string[]; rows: string[][] };

// CSV carries cell text only; quoting every field preserves commas, quotes and newlines.
export function serializeOfficeCsv(table: CsvTable): string {
  return [table.headers, ...table.rows]
    .map((row) => row.map((value) => `"${value.replace(/"/g, '""')}"`).join(","))
    .join("\r\n") + "\r\n";
}

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
  if (table.headers.length < 1 || table.headers.length > 20 || table.headers.some((name) => !name.trim() || [...name].length > 80)) throw new Error("表头须有 1–20 个非空列名，每个最多 80 字符");
  if (new Set(table.headers.map((name) => name.toLowerCase())).size !== table.headers.length) throw new Error("列名不能重复（不区分大小写）");
  if (kinds.length !== table.headers.length || kinds.some((kind) => !["text", "number", "boolean"].includes(kind))) throw new Error("每列都必须选择有效类型");
  if (table.rows.length > 1000 || (table.rows.length + 1) * table.headers.length > 5000) throw new Error("最多 1000 数据行、含表头最多 5000 格");
  if (table.rows.some((row) => row.length !== table.headers.length)) throw new Error("每行的列数须与表头一致");
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

// Decimal coefficients avoid binary floating-point arithmetic when creating static cells.
function decimalCoefficient(value: number): { coefficient: bigint; exponent: number } {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/.exec(String(value));
  if (!match) throw new Error("数字格式无效");
  return { coefficient: BigInt(`${match[1]}${match[2]}${match[3] || ""}`),
    exponent: Number(match[4] || 0) - (match[3] || "").length };
}

export function calculateOfficeColumn(table: CsvTable, name: string, left: number, right: number,
  operation: "add" | "subtract" | "multiply"): CsvTable {
  if (!Number.isInteger(left) || !Number.isInteger(right) || left < 0 || right < 0 ||
      left >= table.headers.length || right >= table.headers.length) throw new Error("请选择两个数据列");
  const operands = csvToOfficeSpec({ headers: ["左操作数", "右操作数"],
    rows: table.rows.map((row) => [row[left], row[right]]) }, ["number", "number"]);
  const rows = table.rows.map((row, index) => {
    const [a, b] = operands.rows[index];
    if (a === null || b === null) return [...row, ""];
    const x = decimalCoefficient(a as number), y = decimalCoefficient(b as number);
    let coefficient: bigint, exponent: number;
    if (operation === "multiply") { coefficient = x.coefficient * y.coefficient; exponent = x.exponent + y.exponent; }
    else {
      exponent = Math.min(x.exponent, y.exponent);
      const first = x.coefficient * 10n ** BigInt(x.exponent - exponent);
      const second = y.coefficient * 10n ** BigInt(y.exponent - exponent);
      coefficient = operation === "add" ? first + second : first - second;
    }
    return [...row, coefficient === 0n ? "0" : `${coefficient}e${exponent}`];
  });
  const next = { headers: [...table.headers, name], rows };
  // Validate the complete table bounds and each calculated number before editing the draft.
  csvToOfficeSpec({ headers: next.headers, rows: next.rows },
    [...table.headers.map(() => "text" as const), "number"]);
  return next;
}

// Group values by their selected column type and materialize static totals.
export function groupOfficeTable(table: CsvTable, kinds: Kind[], key: number | number[], metric: number,
  operation: "count" | "sum"): { table: CsvTable; kinds: Kind[] } {
  const keys = Array.isArray(key) ? key : [key];
  if (!keys.length || keys.length > 19 || new Set(keys).size !== keys.length ||
      keys.some((column) => !Number.isInteger(column) || column < 0 || column >= table.headers.length) ||
      !Number.isInteger(metric) || metric < 0 || metric >= table.headers.length) throw new Error("请选择有效的列");
  if (operation === "sum" && kinds[metric] !== "number") throw new Error("求和列必须设置为数字类型");
  const source = csvToOfficeSpec(table, kinds);
  const groups = new Map<string, { key: string[]; count: number; values: number[] }>();
  source.rows.forEach((row, index) => {
    const value = JSON.stringify(keys.map((column) => row[column]));
    let group = groups.get(value);
    if (!group) {
      group = { key: keys.map((column) => table.rows[index][column]), count: 0, values: [] };
      groups.set(value, group);
    }
    group.count++;
    if (operation === "sum" && row[metric] !== null) group.values.push(row[metric] as number);
  });
  const metricName = operation === "count" ? "行数" : `${table.headers[metric]}求和`;
  if (keys.some((column) => metricName.toLowerCase() === table.headers[column].toLowerCase())) throw new Error("分组列名与结果列名相同，请先重命名列");
  const rows = Array.from(groups.values(), (group) => {
    if (operation === "count") return [...group.key, String(group.count)];
    if (!group.values.length) return [...group.key, ""];
    const values = group.values.map(decimalCoefficient);
    const exponent = Math.min(...values.map((value) => value.exponent));
    const total = values.reduce((sum, value) => sum + value.coefficient * 10n ** BigInt(value.exponent - exponent), 0n);
    return [...group.key, total === 0n ? "0" : `${total}e${exponent}`];
  });
  const result = { headers: [...keys.map((column) => table.headers[column]), metricName], rows };
  const resultKinds: Kind[] = [...keys.map((column) => kinds[column]), "number"];
  csvToOfficeSpec(result, resultKinds);
  return { table: result, kinds: resultKinds };
}
