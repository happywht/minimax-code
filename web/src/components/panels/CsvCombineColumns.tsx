import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { CsvTable } from "./officeCsv";

export function CsvCombineColumns({ table, onApply }: { table: CsvTable; onApply: (next: CsvTable) => void }): JSX.Element {
  const [columns, setColumns] = useState<number[]>([]);
  const [name, setName] = useState("拼接结果"), [separator, setSeparator] = useState(" ");
  const [skipEmpty, setSkipEmpty] = useState(false);
  useEffect(() => { setColumns([]); }, [table.headers]);
  const values = table.rows.map((row) => {
    const parts = columns.map((column) => row[column]);
    return (skipEmpty ? parts.filter((part) => part !== "") : parts).join(separator);
  });
  const next = { headers: [...table.headers, name], rows: table.rows.map((row, index) => [...row, values[index]]) };
  let error = "";
  if (columns.length < 2) error = "请选择至少两列。";
  else if (!name.trim() || Array.from(name).length > 80 || table.headers.some((header) => header.toLowerCase() === name.toLowerCase())) error = "新列名须非空、不超过 80 字符且不能与已有列名重复（不区分大小写）。";
  else if (next.headers.length > 20 || (next.rows.length + 1) * next.headers.length > 5000) error = "新增列会超过 20 列或 5000 格限制。";
  else if (values.some((value) => Array.from(value).length > 4096)) error = "拼接结果有单元格超过 4096 字符。";
  else if (new TextEncoder().encode(JSON.stringify(next)).length > 1048576) error = "新增列后草稿超过 1 MiB。";
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>拼接多列</legend>
    <p>按当前表格列顺序拼接原始文本，保留来源列，新列为文本。空字符串默认参与拼接；可选择跳过空字符串，空格不视为空。结果是静态快照，不随来源列更改自动更新；只改草稿，可撤销。</p>
    <div className="flex flex-wrap gap-3">{table.headers.map((header, index) => <label key={index}><input type="checkbox" checked={columns.includes(index)} onChange={(event) => setColumns((old) => event.target.checked ? [...old, index].sort((a, b) => a - b) : old.filter((item) => item !== index))} /> 第 {index + 1} 列（{header || "未命名"}）</label>)}</div>
    <div className="flex flex-wrap gap-2"><label>新列名 <input aria-label="拼接新列名" value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>分隔文本 <input aria-label="拼接分隔文本" maxLength={100} value={separator} onChange={(event) => setSeparator(event.target.value)} /></label>
      <label><input type="checkbox" checked={skipEmpty} onChange={(event) => setSkipEmpty(event.target.checked)} /> 跳过空字符串</label></div>
    <p role="status">{error || `预览：新增一列，共 ${values.length} 行。`}</p>
    {!error && values.length > 0 && <div><p>前 {Math.min(values.length, 10)} 行结果：</p><ol className="max-h-40 overflow-auto">{values.slice(0, 10).map((value, index) => <li key={index} className="break-all">第 {index + 2} 行：{JSON.stringify(value)}</li>)}</ol></div>}
    <Button type="button" variant="secondary" disabled={!!error || !table.rows.length} onClick={() => onApply(next)}>添加拼接列到草稿</Button>
  </fieldset>;
}
