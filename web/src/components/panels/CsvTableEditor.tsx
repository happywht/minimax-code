import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { CsvTable } from "./officeCsv";

const PAGE_SIZE = 20;

export function CsvTableEditor({ table, original, onChange }: { table: CsvTable; original: CsvTable; onChange: (next: CsvTable) => void }): JSX.Element {
  const [page, setPage] = useState(0);
  const lastPage = Math.max(0, Math.ceil(table.rows.length / PAGE_SIZE) - 1);
  useEffect(() => { setPage((current) => Math.min(current, lastPage)); }, [lastPage]);
  const edit = (row: number, column: number, value: string) => onChange({ ...table, rows: table.rows.map((cells, i) =>
    i === row ? cells.map((old, j) => j === column ? value : old) : cells) });
  const remove = (row: number) => onChange({ ...table, rows: table.rows.filter((_, i) => i !== row) });
  const empty = table.rows.filter((cells) => cells.every((cell) => cell === "")).length;
  return <div className="space-y-2" data-testid="csv-table-editor">
    <p>编辑的数据仅在本页面；行号以当前表格为准（含表头），源 CSV 不变。可以恢复到本次读取的原始表格。</p>
    <div className="flex flex-wrap gap-2"><Button type="button" variant="secondary" disabled={!empty} onClick={() =>
      onChange({ ...table, rows: table.rows.filter((cells) => cells.some((cell) => cell !== "")) })}>移除全空行（{empty}）</Button>
      <Button type="button" variant="secondary" onClick={() => { onChange(original); setPage(0); }}>恢复读取时的表格</Button></div>
    {table.rows.length ? <div className="overflow-auto"><table className="text-xs"><thead><tr><th scope="col">表格行</th>
      {table.headers.map((header, index) => <th key={index} scope="col">{header}</th>)}<th scope="col">操作</th></tr></thead>
      <tbody>{table.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((cells, local) => {
        const index = page * PAGE_SIZE + local;
        return <tr key={index}><th scope="row">{index + 2}</th>{cells.map((value, column) => <td key={column}>
          <textarea aria-label={`表格第 ${index + 2} 行第 ${column + 1} 列`} className="min-w-32 rounded border border-line bg-surface-2 p-1" rows={2} value={value}
            onChange={(event) => edit(index, column, event.target.value)} />
        </td>)}<td><button type="button" onClick={() => remove(index)} aria-label={`删除表格第 ${index + 2} 行`}>删除行</button></td></tr>;
      })}</tbody></table></div> : <p>没有数据行，仍可生成只有表头的表格。</p>}
    <div className="flex items-center gap-2"><Button type="button" variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</Button>
      <span>第 {page + 1} / {lastPage + 1} 页 · {table.rows.length} 行</span>
      <Button type="button" variant="ghost" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>下一页</Button></div>
  </div>;
}
