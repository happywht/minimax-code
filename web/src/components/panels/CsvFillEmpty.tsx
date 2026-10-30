import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { OfficeTableSpec } from "../../types/ipc";
import { csvToOfficeSpec, type CsvTable } from "./officeCsv";

type Kind = OfficeTableSpec["columns"][number]["kind"];

export function CsvFillEmpty({ table, kinds, onApply }: { table: CsvTable; kinds: Kind[]; onApply: (next: CsvTable) => void }): JSX.Element {
  const [column, setColumn] = useState(0), [value, setValue] = useState("");
  useEffect(() => { setColumn(0); }, [table.headers]);
  const changed = table.rows.flatMap((row, index) => row[column] === "" ? [index] : []);
  const next = { ...table, rows: table.rows.map((row) => row[column] === "" ? row.map((cell, index) => index === column ? value : cell) : row) };
  let error = "";
  try {
    if (!value) throw new Error("请输入非空填充值。");
    if (!kinds[column]) throw new Error("列类型不可用。");
    csvToOfficeSpec({ headers: ["填充值"], rows: [[value]] }, [kinds[column]]);
    if (new TextEncoder().encode(JSON.stringify(next)).length > 1048576) throw new Error("填充后草稿超过 1 MiB。");
  } catch (reason) { error = reason instanceof Error ? reason.message : "填充值无效"; }
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>用指定值填充空单元格</legend>
    <p>只填所选列的空字符串，空格不算空；不会修改其他单元格或列类型。数字和布尔值按当前列类型校验，文本按原文保留，不计算公式。操作只改变草稿，可撤销。</p>
    <div className="flex flex-wrap gap-2"><label>填充列 <select aria-label="指定值填充列" value={column} onChange={(event) => setColumn(Number(event.target.value))}>
      {table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"} · {kinds[index]}）</option>)}
    </select></label><label>填充值 <textarea aria-label="空值填充值" rows={2} maxLength={8192} value={value} onChange={(event) => setValue(event.target.value)} /></label></div>
    <p role="status">{error || `预览：将填充 ${changed.length} 个单元格。`}</p>
    {!error && changed.length > 0 && <p className="break-all">前 {Math.min(changed.length, 10)} 项：{changed.slice(0, 10).map((index) => `第 ${index + 2} 行`).join("、")}，空 → {JSON.stringify(value)}</p>}
    <Button type="button" variant="secondary" disabled={!!error || !changed.length} onClick={() => onApply(next)}>填充草稿空值</Button>
  </fieldset>;
}
