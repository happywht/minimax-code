import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { CsvTable } from "./officeCsv";

export function CsvSplitColumn({ table, onApply }: { table: CsvTable; onApply: (next: CsvTable) => void }): JSX.Element {
  const [column, setColumn] = useState(0), [delimiter, setDelimiter] = useState("");
  const [leftName, setLeftName] = useState("拆分前"), [rightName, setRightName] = useState("拆分后");
  useEffect(() => { setColumn(0); }, [table.headers]);
  const names = [leftName, rightName];
  const nameKeys = [...table.headers, ...names].map((name) => name.toLowerCase());
  const pairs = table.rows.map((row) => {
    const value = row[column], index = delimiter ? value.indexOf(delimiter) : -1;
    return { index, left: index < 0 ? value : value.slice(0, index), right: index < 0 ? "" : value.slice(index + delimiter.length) };
  });
  const next = { headers: [...table.headers, ...names], rows: table.rows.map((row, index) => [...row, pairs[index].left, pairs[index].right]) };
  let error = "";
  if (!delimiter) error = "请输入非空分隔文本。";
  else if (names.some((name) => !name.trim() || Array.from(name).length > 80) || new Set(nameKeys).size !== nameKeys.length) error = "新列名须非空、不超过 80 字符，且与已有列名互不重复（不区分大小写）。";
  else if (next.headers.length > 20 || (next.rows.length + 1) * next.headers.length > 5000) error = "新增两列会超过 20 列或 5000 格限制。";
  else if (pairs.some((pair) => Array.from(pair.left).length > 4096 || Array.from(pair.right).length > 4096)) error = "拆分结果中有单元格超过 4096 字符。";
  else if (new TextEncoder().encode(JSON.stringify(next)).length > 1048576) error = "新增列后草稿超过 1 MiB。";
  const matched = pairs.filter((pair) => pair.index >= 0).length;
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>按分隔文本拆分列</legend>
    <p>按首次匹配拆分，区分大小写；未匹配时右列为空。保留原列，新列为文本；仅修改草稿，可撤销。</p>
    <div className="flex flex-wrap gap-2">
      <label>来源列 <select aria-label="拆分来源列" value={column} onChange={(event) => setColumn(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"}）</option>)}</select></label>
      <label>分隔文本 <input aria-label="拆分分隔文本" maxLength={100} value={delimiter} onChange={(event) => setDelimiter(event.target.value)} /></label>
      <label>左列名 <input aria-label="拆分左列名" value={leftName} onChange={(event) => setLeftName(event.target.value)} /></label>
      <label>右列名 <input aria-label="拆分右列名" value={rightName} onChange={(event) => setRightName(event.target.value)} /></label>
    </div>
    <p role="status">{error || `预览：${matched} 行匹配，${pairs.length - matched} 行未匹配。`}</p>
    {!error && pairs.length > 0 && <div><p>前 {Math.min(pairs.length, 10)} 行预览（左 / 右）：</p><ol className="max-h-40 overflow-auto">{pairs.slice(0, 10).map((pair, index) => <li key={index} className="break-all">第 {index + 2} 行：{JSON.stringify(pair.left)} / {JSON.stringify(pair.right)}</li>)}</ol></div>}
    <Button type="button" variant="secondary" disabled={!!error || !table.rows.length} onClick={() => onApply(next)}>添加拆分列到草稿</Button>
  </fieldset>;
}
