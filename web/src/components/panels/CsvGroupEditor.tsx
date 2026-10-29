import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { OfficeTableSpec } from "../../types/ipc";
import { groupOfficeTable, type CsvTable } from "./officeCsv";

type Kind = OfficeTableSpec["columns"][number]["kind"];

export function CsvGroupEditor({ table, kinds, onApply }: { table: CsvTable; kinds: Kind[]; onApply: (table: CsvTable, kinds: Kind[]) => void }): JSX.Element {
  const [key, setKey] = useState(0);
  const [metric, setMetric] = useState(0);
  const [operation, setOperation] = useState<"count" | "sum">("count");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setKey(0); setMetric(0); setError(null); }, [table.headers]);
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>按列分组汇总</legend>
    <p>按列类型比较，空值单独成组，按首次出现顺序输出。行数包含所有行；求和忽略空值，全为空时结果为空。汇总替换当前草稿，可撤销，原文件不变。</p>
    <div className="flex flex-wrap gap-2">
      <label>分组列 <select aria-label="分组列" value={key} onChange={(event) => setKey(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>
      <label>汇总方式 <select aria-label="分组汇总方式" value={operation} onChange={(event) => setOperation(event.target.value as typeof operation)}><option value="count">行数</option><option value="sum">求和</option></select></label>
      {operation === "sum" && <label>求和列 <select aria-label="分组求和列" value={metric} onChange={(event) => setMetric(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>}
      <Button type="button" variant="secondary" disabled={!table.rows.length} onClick={() => {
        try { const result = groupOfficeTable(table, kinds, key, metric, operation); onApply(result.table, result.kinds); setError(null); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "分组失败"); }
      }}>生成分组汇总草稿</Button>
    </div>
    {error && <p role="alert" className="text-status-error">{error}；表格未更改。</p>}
  </fieldset>;
}
