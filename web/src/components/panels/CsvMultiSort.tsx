import { useEffect, useState } from "react";
import { Button } from "../../ui";
import { csvToOfficeSpec, type CsvTable } from "./officeCsv";

type SortKey = { column: number; kind: "text" | "number"; direction: "asc" | "desc" };
const initialKey = (): SortKey => ({ column: 0, kind: "text", direction: "asc" });

export function CsvMultiSort({ table, onApply }: { table: CsvTable; onApply: (next: CsvTable) => void }): JSX.Element {
  const [keys, setKeys] = useState<SortKey[]>([initialKey()]);
  useEffect(() => { setKeys([initialKey()]); }, [table.headers]);
  const change = (index: number, patch: Partial<SortKey>) => setKeys((old) => old.map((key, i) => i === index ? { ...key, ...patch } : key));
  let sorted: { row: string[]; index: number }[] | null = null;
  let error = "";
  try {
    if (new Set(keys.map((key) => key.column)).size !== keys.length) throw new Error("排序列不能重复");
    const values = keys.map((key) => {
      if (key.column < 0 || key.column >= table.headers.length) throw new Error("请选择有效的排序列");
      if (key.kind === "number") return csvToOfficeSpec({ headers: ["排序列"], rows: table.rows.map((row) => [row[key.column]]) }, ["number"]).rows.map((row) => row[0]);
      return table.rows.map((row) => row[key.column] === "" ? null : row[key.column]);
    });
    sorted = table.rows.map((row, index) => ({ row, index }));
    sorted.sort((a, b) => {
      for (let i = 0; i < keys.length; i++) {
        const x = values[i][a.index], y = values[i][b.index];
        if (x === null || y === null) {
          if (x !== y) return x === null ? 1 : -1;
          continue;
        }
        const compared = typeof x === "number" && typeof y === "number"
          ? (x < y ? -1 : x > y ? 1 : 0)
          : (String(x) < String(y) ? -1 : String(x) > String(y) ? 1 : 0);
        if (compared) return keys[i].direction === "asc" ? compared : -compared;
      }
      return a.index - b.index;
    });
  } catch (reason) { error = reason instanceof Error ? reason.message : "排序条件无效"; }
  const changed = sorted?.some((item, index) => item.index !== index) ?? false;
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>多列排序</legend>
    <p>按条件顺序排序，空字符串排最后，同键保持原顺序。文本按 UTF-16 比较；数字需符合安全精度。仅修改草稿，可撤销。</p>
    {keys.map((key, index) => <div key={index} className="flex flex-wrap items-end gap-2">
      <span>优先级 {index + 1}</span>
      <label>列 <select aria-label={`多列排序 ${index + 1} 列`} value={key.column} onChange={(event) => change(index, { column: Number(event.target.value) })}>
        {table.headers.map((name, column) => <option key={column} value={column}>第 {column + 1} 列（{name || "未命名"}）</option>)}
      </select></label>
      <label>比较方式 <select aria-label={`多列排序 ${index + 1} 比较方式`} value={key.kind} onChange={(event) => change(index, { kind: event.target.value as SortKey["kind"] })}><option value="text">文本</option><option value="number">数字</option></select></label>
      <label>方向 <select aria-label={`多列排序 ${index + 1} 方向`} value={key.direction} onChange={(event) => change(index, { direction: event.target.value as SortKey["direction"] })}><option value="asc">升序</option><option value="desc">降序</option></select></label>
      <Button type="button" variant="ghost" disabled={keys.length <= 1} onClick={() => setKeys((old) => old.filter((_, i) => i !== index))}>删除排序条件 {index + 1}</Button>
    </div>)}
    <Button type="button" variant="secondary" disabled={keys.length >= Math.min(3, table.headers.length)} onClick={() => setKeys((old) => [...old, { ...initialKey(), column: table.headers.findIndex((_name, index) => !old.some((key) => key.column === index)) }])}>添加排序条件</Button>
    {error ? <p role="alert">{error}；草稿未更改。</p> : <p role="status">{changed ? "顺序将改变。" : "当前顺序无需更改。"}</p>}
    {sorted && sorted.length > 0 && <details><summary>预览排序后前 5 行</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{sorted.slice(0, 5).map((item) => `原表格第 ${item.index + 2} 行：${JSON.stringify(item.row)}`).join("\n")}</pre></details>}
    <Button type="button" variant="secondary" disabled={!sorted || !changed} onClick={() => { if (sorted) onApply({ ...table, rows: sorted.map((item) => item.row) }); }}>应用多列排序到草稿</Button>
  </fieldset>;
}
