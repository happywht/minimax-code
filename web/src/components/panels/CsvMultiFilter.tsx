import { useEffect, useState } from "react";
import { Button } from "../../ui";
import { csvToOfficeSpec, type CsvTable } from "./officeCsv";

type Condition = { column: number; operation: "equals" | "contains" | "empty" | "not_empty" | "number_range"; value: string; minimum: string; maximum: string };
const condition = (): Condition => ({ column: 0, operation: "equals", value: "", minimum: "", maximum: "" });

export function CsvMultiFilter({ table, onApply }: { table: CsvTable; onApply: (table: CsvTable) => void }): JSX.Element {
  const [mode, setMode] = useState<"all" | "any">("all"), [conditions, setConditions] = useState<Condition[]>([condition(), condition()]);
  useEffect(() => { setConditions([condition(), condition()]); }, [table.headers]);
  const change = (index: number, patch: Partial<Condition>) => setConditions((previous) => previous.map((item, i) => i === index ? { ...item, ...patch } : item));
  let matches: string[][] | null = null, error = "";
  try {
    const predicates = conditions.map((item, conditionIndex): ((row: string[], index: number) => boolean) => {
      if (!Number.isInteger(item.column) || item.column < 0 || item.column >= table.headers.length) throw new Error(`条件 ${conditionIndex + 1} 的列无效`);
      if (item.operation === "contains" && !item.value) throw new Error(`条件 ${conditionIndex + 1} 的包含文字不能为空`);
      if (item.operation === "number_range") {
        if (!item.minimum && !item.maximum) throw new Error(`条件 ${conditionIndex + 1} 至少填写一个数值边界`);
        const [low, high] = csvToOfficeSpec({ headers: ["边界"], rows: [[item.minimum], [item.maximum]] }, ["number"]).rows.map((row) => row[0]);
        const numbers = csvToOfficeSpec({ headers: ["数值"], rows: table.rows.map((row) => [row[item.column]]) }, ["number"]).rows.map((row) => row[0]);
        if ((low !== null && typeof low !== "number") || (high !== null && typeof high !== "number") ||
            (typeof low === "number" && typeof high === "number" && low > high)) throw new Error(`条件 ${conditionIndex + 1} 的边界无效`);
        return (_row, index) => {
          const value = numbers[index];
          return typeof value === "number" && (low === null || (typeof low === "number" && value >= low)) &&
            (high === null || (typeof high === "number" && value <= high));
        };
      }
      return (row) => {
        const value = row[item.column];
        if (item.operation === "empty") return value === "";
        if (item.operation === "not_empty") return value !== "";
        return item.operation === "contains" ? value.includes(item.value) : value === item.value;
      };
    });
    matches = table.rows.filter((row, index) => mode === "all" ? predicates.every((predicate) => predicate(row, index)) : predicates.some((predicate) => predicate(row, index)));
  } catch (reason) { error = reason instanceof Error ? reason.message : "筛选条件无效"; }
  return <fieldset className="space-y-2 rounded border border-line p-2">
    <legend>组合条件筛选</legend>
    <p>最多 5 个条件，选择全部满足或任一满足。文本比较区分大小写、不去空白；等于空文本会匹配空字符串。数值边界包含端点、空单元格不匹配；所有数值条件的整列须符合安全精度，即使另一条件已匹配也会校验。列结构或列名变化会重置条件。应用仅修改草稿，可撤销，原文件不变。</p>
    <label>组合方式 <select aria-label="组合筛选方式" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="all">全部满足（且）</option><option value="any">任一满足（或）</option></select></label>
    {conditions.map((item, index) => <div key={index} className="flex flex-wrap items-end gap-2 rounded border border-line p-2">
      <span>条件 {index + 1}</span><label>列 <select aria-label={`组合筛选条件 ${index + 1} 列`} value={item.column} onChange={(event) => change(index, { column: Number(event.target.value) })}>
        {table.headers.map((name, column) => <option key={column} value={column}>第 {column + 1} 列（{name || "未命名"}）</option>)}</select></label>
      <label>条件 <select aria-label={`组合筛选条件 ${index + 1} 操作`} value={item.operation} onChange={(event) => change(index, { operation: event.target.value as Condition["operation"] })}>
        <option value="equals">完全等于</option><option value="contains">包含</option><option value="empty">为空</option><option value="not_empty">不为空</option><option value="number_range">数值区间</option></select></label>
      {(item.operation === "equals" || item.operation === "contains") && <label>文本 <input aria-label={`组合筛选条件 ${index + 1} 文本`} value={item.value} onChange={(event) => change(index, { value: event.target.value })} /></label>}
      {item.operation === "number_range" && <><label>下限 <input aria-label={`组合筛选条件 ${index + 1} 下限`} value={item.minimum} onChange={(event) => change(index, { minimum: event.target.value })} /></label>
        <label>上限 <input aria-label={`组合筛选条件 ${index + 1} 上限`} value={item.maximum} onChange={(event) => change(index, { maximum: event.target.value })} /></label></>}
      <Button type="button" variant="ghost" disabled={conditions.length <= 2} onClick={() => setConditions((previous) => previous.filter((_, i) => i !== index))}>删除此条件</Button>
    </div>)}
    <Button type="button" variant="secondary" disabled={conditions.length >= 5} onClick={() => setConditions((previous) => [...previous, condition()])}>添加筛选条件</Button>
    <p role="status">{error || (matches && `预览：保留 ${matches.length} 行，移除 ${table.rows.length - matches.length} 行。`)}</p>
    {matches && matches.length > 0 && <details><summary>预览前 5 行匹配数据</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{matches.slice(0, 5).map((row) => JSON.stringify(row)).join("\n")}</pre></details>}
    <Button type="button" variant="secondary" disabled={!matches || matches.length === table.rows.length} onClick={() => { if (matches) onApply({ ...table, rows: matches }); }}>应用组合筛选到草稿</Button>
  </fieldset>;
}
