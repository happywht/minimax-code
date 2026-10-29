import { useEffect, useState } from "react";
import { Button } from "../../ui";
import type { OfficeTableSpec } from "../../types/ipc";
import { calculateOfficeColumn, csvToOfficeSpec, type CsvTable } from "./officeCsv";

const PAGE_SIZE = 20;
const HISTORY_LIMIT = 20;

type Kind = OfficeTableSpec["columns"][number]["kind"];
type Snapshot = { table: CsvTable; kinds: Kind[] };

export function CsvTableEditor({ table, original, kinds, onChange: applyChange }: { table: CsvTable; original: CsvTable; kinds: Kind[]; onChange: (next: CsvTable, nextKinds?: Kind[]) => void }): JSX.Element {
  const [page, setPage] = useState(0);
  const [dedupeColumns, setDedupeColumns] = useState<number[]>([]);
  const [sortColumn, setSortColumn] = useState(0);
  const [sortKind, setSortKind] = useState<"text" | "number">("text");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [sortError, setSortError] = useState<string | null>(null);
  const [calculationName, setCalculationName] = useState("计算结果");
  const [calculationLeft, setCalculationLeft] = useState(0);
  const [calculationRight, setCalculationRight] = useState(0);
  const [calculationOperation, setCalculationOperation] = useState<"add" | "subtract" | "multiply">("multiply");
  const [calculationError, setCalculationError] = useState<string | null>(null);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const snapshot = (): Snapshot => ({ table, kinds: [...kinds] });
  const onChange = (next: CsvTable, nextKinds?: Kind[]) => {
    setPast((history) => [...history, snapshot()].slice(-HISTORY_LIMIT));
    setFuture([]);
    applyChange(next, nextKinds);
  };
  const undo = () => {
    const previous = past[past.length - 1];
    if (!previous) return;
    setPast(past.slice(0, -1));
    setFuture((history) => [...history, snapshot()].slice(-HISTORY_LIMIT));
    applyChange(previous.table, previous.kinds);
  };
  const redo = () => {
    const next = future[future.length - 1];
    if (!next) return;
    setFuture(future.slice(0, -1));
    setPast((history) => [...history, snapshot()].slice(-HISTORY_LIMIT));
    applyChange(next.table, next.kinds);
  };
  const lastPage = Math.max(0, Math.ceil(table.rows.length / PAGE_SIZE) - 1);
  useEffect(() => { setPage((current) => Math.min(current, lastPage)); }, [lastPage]);
  useEffect(() => { setPage(0); setPast([]); setFuture([]); }, [original]);
  // Column edits can change indices, so require the user to select keys again.
  useEffect(() => { setDedupeColumns([]); setSortColumn(0); setSortError(null); setCalculationLeft(0); setCalculationRight(0); setCalculationError(null); }, [table.headers]);
  const sortRows = () => {
    try {
      const values = sortKind === "number"
        ? csvToOfficeSpec({ headers: ["排序列"], rows: table.rows.map((row) => [row[sortColumn]]) }, ["number"]).rows.map((row) => row[0])
        : table.rows.map((row) => row[sortColumn] === "" ? null : row[sortColumn]);
      const direction = sortDirection === "asc" ? 1 : -1;
      const rows = table.rows.map((row, index) => ({ row, index, value: values[index] }));
      rows.sort((a, b) => {
        if (a.value === null || b.value === null) return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1;
        const compared = typeof a.value === "number" && typeof b.value === "number"
          ? (a.value < b.value ? -1 : a.value > b.value ? 1 : 0)
          : (String(a.value) < String(b.value) ? -1 : String(a.value) > String(b.value) ? 1 : 0);
        return compared ? compared * direction : a.index - b.index;
      });
      onChange({ ...table, rows: rows.map((item) => item.row) });
      setPage(0); setSortError(null);
    } catch (error) {
      setSortError(error instanceof Error ? error.message : "排序失败，表格未更改");
    }
  };
  const edit = (row: number, column: number, value: string) => onChange({ ...table, rows: table.rows.map((cells, i) =>
    i === row ? cells.map((old, j) => j === column ? value : old) : cells) });
  const remove = (row: number) => onChange({ ...table, rows: table.rows.filter((_, i) => i !== row) });
  const rename = (column: number, value: string) => onChange({ ...table, headers: table.headers.map((old, i) => i === column ? value : old) });
  const move = (column: number, direction: -1 | 1) => {
    const target = column + direction;
    if (target < 0 || target >= table.headers.length) return;
    const swap = <T,>(items: T[]): T[] => {
      const next = [...items]; [next[column], next[target]] = [next[target], next[column]]; return next;
    };
    onChange({ headers: swap(table.headers), rows: table.rows.map(swap) }, swap(kinds));
  };
  const removeColumn = (column: number) => {
    if (table.headers.length <= 1) return;
    onChange({ headers: table.headers.filter((_, i) => i !== column),
      rows: table.rows.map((cells) => cells.filter((_, i) => i !== column)) }, kinds.filter((_, i) => i !== column));
  };
  const addColumn = () => {
    if (table.headers.length >= 20 || (table.rows.length + 1) * (table.headers.length + 1) > 5000) return;
    let index = 1;
    const existing = new Set(table.headers.map((name) => name.toLowerCase()));
    while (existing.has(`新列 ${index}`.toLowerCase())) index++;
    onChange({ headers: [...table.headers, `新列 ${index}`], rows: table.rows.map((cells) => [...cells, ""]) }, [...kinds, "text"]);
  };
  const addRow = () => {
    if (table.rows.length >= 1000 || (table.rows.length + 2) * table.headers.length > 5000) return;
    onChange({ ...table, rows: [...table.rows, table.headers.map(() => "")] });
    setPage(Math.floor(table.rows.length / PAGE_SIZE));
  };
  const trimColumn = (column: number) => onChange({ ...table, rows: table.rows.map((cells) =>
    cells.map((value, i) => i === column ? value.trim() : value)) });
  const empty = table.rows.filter((cells) => cells.every((cell) => cell === "")).length;
  const seen = new Set<string>();
  const uniqueRows = table.rows.filter((cells) => {
    const key = JSON.stringify(dedupeColumns.map((column) => cells[column]));
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const duplicateCount = dedupeColumns.length ? table.rows.length - uniqueRows.length : 0;
  return <div className="space-y-2" data-testid="csv-table-editor">
    <p>编辑的数据仅在本页面；行号以当前表格为准（含表头），源 CSV 不变。恢复会还原本次读取的表格并将列类型重置为文本。</p>
    <p>可撤销最近 20 次表格编辑，包括列类型快照；重新读取表格或离开编辑器后历史会清空。</p>
    <p>列清理会去除该列每个数据单元格的首尾空白，可能改变文本；不会自动清理。</p>
    <div className="flex flex-wrap gap-2"><Button type="button" variant="secondary" disabled={!past.length} onClick={undo}>撤销表格编辑</Button>
      <Button type="button" variant="secondary" disabled={!future.length} onClick={redo}>重做表格编辑</Button>
      <Button type="button" variant="secondary" disabled={table.rows.length >= 1000 || (table.rows.length + 2) * table.headers.length > 5000} onClick={addRow}>新增空行</Button>
      <Button type="button" variant="secondary" disabled={table.headers.length >= 20 || (table.rows.length + 1) * (table.headers.length + 1) > 5000} onClick={addColumn}>新增文本列</Button>
      <Button type="button" variant="secondary" disabled={!empty} onClick={() =>
      onChange({ ...table, rows: table.rows.filter((cells) => cells.some((cell) => cell !== "")) })}>移除全空行（{empty}）</Button>
      <Button type="button" variant="secondary" onClick={() => { onChange(original, original.headers.map(() => "text" as const)); setPage(0); }}>恢复读取时的表格</Button></div>
    <fieldset className="space-y-2 rounded border border-line p-2">
      <legend>逐行计算新列</legend>
      <p>按当前草稿的两列计算静态数值，空值产生空结果；不自动更新，修改源数据后需重新计算。无效数字或超出安全精度时整次操作不会改变草稿，可撤销。</p>
      <div className="flex flex-wrap gap-2">
        <label>新列名 <input aria-label="计算列名称" value={calculationName} onChange={(event) => setCalculationName(event.target.value)} /></label>
        <label>左列 <select aria-label="计算左列" value={calculationLeft} onChange={(event) => setCalculationLeft(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>
        <label>运算 <select aria-label="计算运算" value={calculationOperation} onChange={(event) => setCalculationOperation(event.target.value as typeof calculationOperation)}><option value="add">加</option><option value="subtract">减</option><option value="multiply">乘</option></select></label>
        <label>右列 <select aria-label="计算右列" value={calculationRight} onChange={(event) => setCalculationRight(Number(event.target.value))}>{table.headers.map((header, index) => <option key={index} value={index}>{header || `第 ${index + 1} 列`}</option>)}</select></label>
        <Button type="button" variant="secondary" disabled={table.headers.length >= 20 || (table.rows.length + 1) * (table.headers.length + 1) > 5000} onClick={() => {
          try { const next = calculateOfficeColumn(table, calculationName, calculationLeft, calculationRight, calculationOperation); onChange(next, [...kinds, "number"]); setCalculationError(null); }
          catch (error) { setCalculationError(error instanceof Error ? error.message : "计算失败"); }
        }}>添加计算列</Button>
      </div>
      {calculationError && <p role="alert" className="text-status-error">{calculationError}；表格未更改。</p>}
    </fieldset>
    <fieldset className="space-y-2 rounded border border-line p-2">
      <legend>按列排序</legend>
      <p>文本按 UTF-16 码元顺序、区分大小写；数字使用表格的安全精度规则，非法数字会拒绝排序。空字符串始终排在最后，相同值保持原顺序。只改变草稿，可撤销。</p>
      <div className="flex flex-wrap gap-2">
        <label>排序列 <select aria-label="排序列" value={sortColumn} onChange={(event) => setSortColumn(Number(event.target.value))}>
          {table.headers.map((header, index) => <option key={index} value={index}>第 {index + 1} 列（{header || "未命名"}）</option>)}
        </select></label>
        <label>比较方式 <select aria-label="排序比较方式" value={sortKind} onChange={(event) => setSortKind(event.target.value as "text" | "number")}><option value="text">文本</option><option value="number">数字</option></select></label>
        <label>方向 <select aria-label="排序方向" value={sortDirection} onChange={(event) => setSortDirection(event.target.value as "asc" | "desc")}><option value="asc">升序</option><option value="desc">降序</option></select></label>
        <Button type="button" variant="secondary" disabled={table.rows.length < 2} onClick={sortRows}>排序草稿</Button>
      </div>
      {sortError && <p role="alert" className="text-status-error">{sortError}；表格未更改。</p>}
    </fieldset>
    <fieldset className="space-y-2 rounded border border-line p-2">
      <legend>按列去重</legend>
      <p>选择关键列后预览待删除数量；所有所选列的原始文本完全相同才算重复，空字符串也参与匹配。保留当前顺序中的第一行，不自动去除空白或忽略大小写。</p>
      <div className="flex flex-wrap gap-3">{table.headers.map((header, index) => <label key={index}>
        <input type="checkbox" checked={dedupeColumns.includes(index)} onChange={(event) => setDedupeColumns((old) =>
          event.target.checked ? [...old, index].sort((a, b) => a - b) : old.filter((column) => column !== index))} /> 第 {index + 1} 列（{header || "未命名"}）
      </label>)}</div>
      <p role="status">{dedupeColumns.length ? `预览：将删除 ${duplicateCount} 行，保留 ${uniqueRows.length} 行。` : "请选择至少一列以预览重复行。"}</p>
      <Button type="button" variant="secondary" disabled={!dedupeColumns.length || !duplicateCount} onClick={() => { onChange({ ...table, rows: uniqueRows }); setPage(0); }}>删除预览中的重复行（{duplicateCount}）</Button>
    </fieldset>
    <div className="overflow-auto"><table className="text-xs"><thead><tr><th scope="col">表格行</th>
      {table.headers.map((header, index) => <th key={index} scope="col" className="min-w-40 align-top">
        <input aria-label={`第 ${index + 1} 列名称`} className="w-full rounded border border-line bg-surface-2 p-1" value={header} onChange={(event) => rename(index, event.target.value)} />
        <div className="flex flex-wrap gap-1"><button type="button" disabled={index === 0} onClick={() => move(index, -1)} aria-label={`第 ${index + 1} 列左移`}>左移</button>
          <button type="button" disabled={index === table.headers.length - 1} onClick={() => move(index, 1)} aria-label={`第 ${index + 1} 列右移`}>右移</button>
          <button type="button" disabled={table.headers.length === 1} onClick={() => removeColumn(index)} aria-label={`删除第 ${index + 1} 列`}>删列</button>
          <button type="button" disabled={!table.rows.some((cells) => cells[index] !== cells[index].trim())} onClick={() => trimColumn(index)} aria-label={`去除第 ${index + 1} 列首尾空白`}>去首尾空白</button></div>
      </th>)}<th scope="col">操作</th></tr></thead>
      <tbody>{table.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((cells, local) => {
        const index = page * PAGE_SIZE + local;
        return <tr key={index}><th scope="row">{index + 2}</th>{cells.map((value, column) => <td key={column}>
          <textarea aria-label={`表格第 ${index + 2} 行第 ${column + 1} 列`} className="min-w-32 rounded border border-line bg-surface-2 p-1" rows={2} value={value}
            onChange={(event) => edit(index, column, event.target.value)} />
        </td>)}<td><button type="button" onClick={() => remove(index)} aria-label={`删除表格第 ${index + 2} 行`}>删除行</button></td></tr>;
      })}</tbody></table></div>
    {!table.rows.length && <p>没有数据行，仍可生成只有表头的表格。</p>}
    <div className="flex items-center gap-2"><Button type="button" variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</Button>
      <span>第 {page + 1} / {lastPage + 1} 页 · {table.rows.length} 行</span>
      <Button type="button" variant="ghost" disabled={page >= lastPage} onClick={() => setPage(page + 1)}>下一页</Button></div>
  </div>;
}
