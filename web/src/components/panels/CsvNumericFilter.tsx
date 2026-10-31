import { useEffect, useState } from "react";
import { Button } from "../../ui";
import { csvToOfficeSpec, type CsvTable } from "./officeCsv";

export function CsvNumericFilter({ table, onApply }: { table: CsvTable; onApply: (table: CsvTable) => void }): JSX.Element {
  const [column, setColumn] = useState(0);
  const [minimum, setMinimum] = useState("");
  const [maximum, setMaximum] = useState("");
  useEffect(() => { setColumn(0); }, [table.headers]);
  let rows: string[][] | null = null;
  let error = "";
  try {
    if (!minimum && !maximum) throw new Error("请填写至少一个边界。");
    const [low, high] = csvToOfficeSpec({ headers: ["边界"], rows: [[minimum], [maximum]] }, ["number"]).rows.map((row) => row[0]);
    const numbers = csvToOfficeSpec({ headers: ["数值"], rows: table.rows.map((row) => [row[column]]) }, ["number"]).rows.map((row) => row[0]);
    if ((low !== null && typeof low !== "number") || (high !== null && typeof high !== "number")) throw new Error("边界不是有效数字。");
    if (typeof low === "number" && typeof high === "number" && low > high) throw new Error("下限不能大于上限。");
    rows = table.rows.filter((_, index) => {
      const value = numbers[index];
      return typeof value === "number" && (low === null || (typeof low === "number" && value >= low)) &&
        (high === null || (typeof high === "number" && value <= high));
    });
  } catch (reason) { error = reason instanceof Error ? reason.message : "无法筛选"; }
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>按数值区间筛选</legend>
    <p>边界包含端点，留空表示无边界；空单元格不保留。无效数字会阻止操作；仅修改草稿，可撤销。</p>
    <div className="flex flex-wrap gap-2">
      <label>数值列 <select aria-label="数值筛选列" value={column} onChange={(event) => setColumn(Number(event.target.value))}>
        {table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"}）</option>)}
      </select></label>
      <label>下限 <input aria-label="数值筛选下限" value={minimum} onChange={(event) => setMinimum(event.target.value)} /></label>
      <label>上限 <input aria-label="数值筛选上限" value={maximum} onChange={(event) => setMaximum(event.target.value)} /></label>
    </div>
    <p role="status">{error || (rows && `预览：保留 ${rows.length} 行，移除 ${table.rows.length - rows.length} 行。`)}</p>
    <Button type="button" variant="secondary" disabled={!rows || rows.length === table.rows.length} onClick={() => { if (rows) onApply({ ...table, rows }); }}>应用数值筛选到草稿</Button>
  </fieldset>;
}
