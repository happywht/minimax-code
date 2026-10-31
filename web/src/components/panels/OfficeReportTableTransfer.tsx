import { useEffect, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import { Button, Input } from "../../ui";
import type { ReportTableTransfer } from "./DeliveryWorkspace";
import type { CsvTable } from "./officeCsv";

export function OfficeReportTableTransfer({ table, project, session, service, host, pending, onPrepare }: {
  table: CsvTable; project: string | null; session: string | null; service: string | null; host: string;
  pending: boolean; onPrepare: (transfer: ReportTableTransfer) => void;
}): JSX.Element {
  const [source, setSource] = useState(""), [start, setStart] = useState("1"), [end, setEnd] = useState(String(table.rows.length));
  const [columns, setColumns] = useState<number[]>(() => table.headers.map((_, index) => index));
  const [error, setError] = useState("");
  useEffect(() => { setStart("1"); setEnd(String(table.rows.length)); setColumns(table.headers.map((_, index) => index)); setSource(""); setError(""); }, [table]);
  const prepare = () => {
    const current = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
    if (!project || !service || ipc.agentBaseUrl !== host || current.currentProjectId !== project || current.currentSessionId !== session ||
        office.projectId !== project || office.host !== host || office.serviceId !== service || pending) {
      setError("项目、会话、服务或待处理状态已变化，请重新选择。"); return;
    }
    const first = Number(start), last = Number(end);
    if (!columns.length || columns.some((column) => !Number.isInteger(column) || column < 0 || column >= table.headers.length) ||
        (table.rows.length > 0 && (!/^\d+$/.test(start) || !/^\d+$/.test(end) || !Number.isSafeInteger(first) || !Number.isSafeInteger(last) || first < 1 || last < first || last > table.rows.length || last - first + 1 > 99))) {
      setError("请选择至少一列，并选择有效的数据行范围（最多 99 行）。"); return;
    }
    const explanation = source.trim(), range = table.rows.length ? `当前表格数据行 ${first}–${last}（从 1 开始，不含表头）` : "当前表格无数据行，仅表头";
    const details = `${explanation}；${range}；当前表格列序号 ${columns.map((column) => column + 1).join("、")}`;
    // eslint-disable-next-line no-control-regex
    if (!explanation || [...details].length > 4000 || /[\x00-\x08\x0b-\x1f\x7f-\x9f]/.test(details)) {
      setError("请填写有效来源和处理口径；包含选取范围的说明最多 4000 字符。"); return;
    }
    const selectedRows = table.rows.length ? table.rows.slice(first - 1, last) : [];
    if (table.rows.some((row) => row.length !== table.headers.length) || columns.length > 20) {
      setError("当前表格列数不一致或超过 20 列，请先修正。"); return;
    }
    const rows = [table.headers, ...selectedRows].map((row) => columns.map((column) => row[column]));
    setError(""); onPrepare({ rows, source: details });
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p>选择当前编辑表格的列和连续数据行，连同表头复制到报告待处理区；报告中还需选择章节并确认。</p><p>表格是文本快照，原件不修改，公式不计算，业务内容未经核验。</p>
    <Input aria-label="表格报告来源说明" placeholder="填写原始文件、工作表及筛选或汇总口径" value={source} onChange={(event) => setSource(event.target.value)} />
    <fieldset><legend>选择列（保持当前表格顺序）</legend>{table.headers.map((name, index) => <label key={index} className="mr-3 inline-block"><input type="checkbox" checked={columns.includes(index)} onChange={(event) => {
      const checked = event.target.checked; setColumns((previous) => checked ? [...previous, index].sort((a, b) => a - b) : previous.filter((value) => value !== index));
    }} /> 第 {index + 1} 列：{name}</label>)}</fieldset>
    {table.rows.length ? <div className="flex flex-wrap items-center gap-2"><label>数据起始行（从 1 开始）<Input aria-label="报告表格起始数据行" type="number" min={1} max={table.rows.length} value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label>数据结束行（包含）<Input aria-label="报告表格结束数据行" type="number" min={1} max={table.rows.length} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <span>当前共 {table.rows.length} 行；最多交接 99 行。</span></div> : <p>当前表格只有表头。</p>}
    {pending && <p role="status">已有待处理表格，请先在报告中追加或放弃。</p>}
    <Button type="button" variant="secondary" disabled={!project || !service || pending} onClick={prepare}>准备加入报告</Button>
    {error && <p role="alert">{error}</p>}
  </div>;
}
