import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { CsvTable } from "./officeCsv";

/** Literal, non-overlapping replacement in one data column; headers are untouched. */
export function CsvReplaceEditor({ table, onApply }: { table: CsvTable; onApply: (next: CsvTable) => void }): JSX.Element {
  const [column, setColumn] = useState(0);
  const [find, setFind] = useState("");
  const [replacement, setReplacement] = useState("");
  useEffect(() => { setColumn(0); }, [table.headers]);

  let changed = 0;
  let tooLong = false;
  const rows = table.rows.map((row) => {
    const value = row[column] ?? "";
    if (!find || find === replacement || !value.includes(find)) return row;
    changed++;
    // Build only up to the maximum possible UTF-16 length for 4096 code points.
    // Count the finished string so surrogate pairs formed across boundaries count correctly.
    let position = 0, result = "", index = value.indexOf(find);
    while (index !== -1) {
      result += value.slice(position, index) + replacement;
      if (result.length > 8192) { tooLong = true; return row; }
      position = index + find.length; index = value.indexOf(find, position);
    }
    result += value.slice(position);
    if (result.length > 8192 || [...result].length > 4096) { tooLong = true; return row; }
    const next = [...row];
    next[column] = result;
    return next;
  });

  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>按列查找替换</legend>
    <p>只替换所选列的数据单元格，不改表头；按原文区分大小写，逐个替换不重叠的匹配，不使用正则或特殊替换符。替换文本为空时删除匹配内容；空查找不可应用。原件不变，可撤销。</p>
    <div className="flex flex-wrap gap-2">
      <label>替换列 <select aria-label="替换列" value={column} onChange={(event) => setColumn(Number(event.target.value))}>
        {table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"}）</option>)}
      </select></label>
      <label>查找文本 <textarea aria-label="查找文本" rows={2} maxLength={4096} value={find} onChange={(event) => setFind(event.target.value)} /></label>
      <label>替换文本 <textarea aria-label="替换文本" rows={2} maxLength={4096} value={replacement} onChange={(event) => setReplacement(event.target.value)} /></label>
    </div>
    <p role="status">{!find ? "请输入非空查找文本。" : tooLong ? `预览：${changed} 行匹配；替换后有单元格超过 4096 字符，不能应用。` : `预览：将改变 ${changed} 行。`}</p>
    <Button type="button" variant="secondary" disabled={!find || !changed || tooLong} onClick={() => onApply({ ...table, rows })}>替换草稿中的文本</Button>
  </fieldset>;
}
