import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { OfficeTableSpec } from "../../types/ipc";
import { groupOfficeTable, type CsvTable } from "./officeCsv";

type Kind = OfficeTableSpec["columns"][number]["kind"];

export function CsvGroupEditor({ table, kinds, onApply }: { table: CsvTable; kinds: Kind[]; onApply: (table: CsvTable, kinds: Kind[]) => void }): JSX.Element {
  const [key, setKey] = useState(0);
  const [additionalKeys, setAdditionalKeys] = useState<number[]>([]);
  const [metric, setMetric] = useState(0);
  const [operation, setOperation] = useState<"count" | "sum">("count");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setKey(0); setAdditionalKeys([]); setMetric(0); setError(null); }, [table.headers]);
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>按列分组汇总</legend>
    <p>可按一列或多列组合分组，逐列按类型比较，空值单独成组，按首次出现顺序输出；结果列先是首个分组列，再按原表顺序排列其他分组列，最后为汇总列。行数包含所有行；求和忽略空值，全为空时结果为空。汇总为静态值，替换当前草稿，可撤销，原文件不变。</p>
    <div className="flex flex-wrap gap-2">
      <label>首个分组列 <select aria-label="分组列" value={key} onChange={(event) => { const next = Number(event.target.value); setKey(next); setAdditionalKeys((old) => old.filter((column) => column !== next)); }}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>
      <label>汇总方式 <select aria-label="分组汇总方式" value={operation} onChange={(event) => setOperation(event.target.value as typeof operation)}><option value="count">行数</option><option value="sum">求和</option></select></label>
      {operation === "sum" && <label>求和列 <select aria-label="分组求和列" value={metric} onChange={(event) => setMetric(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>}
      <Button type="button" variant="secondary" disabled={!table.rows.length} onClick={() => {
        try { const result = groupOfficeTable(table, kinds, [key, ...additionalKeys], metric, operation); onApply(result.table, result.kinds); setError(null); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "分组失败"); }
      }}>生成分组汇总草稿</Button>
    </div>
    <fieldset><legend>其他分组列（可选，最多 18 列）</legend><div className="flex flex-wrap gap-2">{table.headers.map((header, index) => index !== key && <label key={index}>
      <input type="checkbox" checked={additionalKeys.includes(index)} disabled={!additionalKeys.includes(index) && additionalKeys.length >= 18} onChange={(event) => {
        const checked = event.target.checked; setAdditionalKeys((old) => checked ? [...old, index].sort((a, b) => a - b) : old.filter((column) => column !== index));
      }} /> 第 {index + 1} 列（{header || "未命名"}）</label>)}</div></fieldset>
    {error && <p role="alert" className="text-status-error">{error}；表格未更改。</p>}
  </fieldset>;
}
