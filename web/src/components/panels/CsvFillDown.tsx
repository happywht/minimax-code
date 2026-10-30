import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { CsvTable } from "./officeCsv";

/** Copy the preceding nonempty raw cell into empty cells in one column. */
export function CsvFillDown({ table, onApply }: { table: CsvTable; onApply: (next: CsvTable) => void }): JSX.Element {
  const [column, setColumn] = useState(0);
  useEffect(() => { setColumn(0); }, [table.headers]);
  let previous = "";
  const changes: Array<{ row: number; value: string }> = [];
  const rows = table.rows.map((row, index) => {
    const value = row[column];
    if (value !== "") { previous = value; return row; }
    if (!previous) return row;
    changes.push({ row: index + 2, value: previous });
    const next = [...row]; next[column] = previous; return next;
  });
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>向下填充空单元格</legend>
    <p>按当前行顺序，将所选列上方最近的非空原文复制到空字符串单元格；开头没有来源的空值保留。空格不是空值，筛选或排序会改变填充来源。仅修改草稿，可撤销；列类型不变，生成时仍会校验类型和精度。</p>
    <label>填充列 <select aria-label="向下填充列" value={column} onChange={(event) => setColumn(Number(event.target.value))}>
      {table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"}）</option>)}
    </select></label>
    <p role="status">预览：将填充 {changes.length} 个单元格。</p>
    {changes.length > 0 && <div><p>前 {Math.min(changes.length, 10)} 项（空值 → 填充值）：</p><ul className="max-h-40 overflow-auto">
      {changes.slice(0, 10).map((change) => <li key={change.row} className="break-all">第 {change.row} 行：空 → {change.value}</li>)}
    </ul></div>}
    <Button type="button" variant="secondary" disabled={!changes.length} onClick={() => onApply({ ...table, rows })}>向下填充草稿</Button>
  </fieldset>;
}
