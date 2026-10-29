import type { OfficeChart, OfficeSummary, OfficeTableSpec } from "../../types/ipc";
import { Button } from "../../ui";

export interface OfficeAnalysisOptions { summaries: OfficeSummary[]; chart: OfficeChart | null }

export function OfficeAnalysisControls({ spec, value, onChange }: { spec: OfficeTableSpec | null; value: OfficeAnalysisOptions; onChange: (next: OfficeAnalysisOptions) => void }): JSX.Element {
  const columns = spec?.columns ?? [];
  const numeric = columns.filter((column) => column.kind === "number");
  const categories = columns.filter((column) => column.kind === "text" || column.kind === "number");
  const patchSummary = (index: number, patch: Partial<OfficeSummary>) => onChange({ ...value,
    summaries: value.summaries.map((row, i) => i === index ? { ...row, ...patch } : row) });
  const patchChart = (patch: Partial<OfficeChart>) => { if (value.chart) onChange({ ...value, chart: { ...value.chart, ...patch } }); };
  return <details className="rounded border border-line p-3">
    <summary>可选：静态汇总和图表</summary>
    <p>配置基于下方 JSON 表格。CSV 编辑后请先点击转换；汇总值写入单元格，不是公式，修改数据不会自动重算。</p>
    <p>计数统计非空值（含 0 和 false）；求和与平均值只接受数字列并忽略空白。空列求和为 0，平均为空；平均按指定小数位十进制四舍五入。</p>
    <p>汇总最多 20 项、原表最多 18 列，并受最终 1000 行及 5000 格限制。</p>
    {!spec && <p role="status">请先提供有效的表格 JSON 才能选择列。</p>}
    {value.summaries.map((summary, index) => {
      const allowed = summary.operation === "count" ? columns : numeric;
      return <div key={index} className="flex flex-wrap items-center gap-2">
        <label>操作<select aria-label={`第 ${index + 1} 项汇总操作`} value={summary.operation} onChange={(event) => patchSummary(index, { operation: event.target.value as OfficeSummary["operation"] })}>
          <option value="sum">求和</option><option value="count">非空计数</option><option value="average">平均值</option></select></label>
        <label>列<select aria-label={`第 ${index + 1} 项汇总列`} value={summary.column} onChange={(event) => patchSummary(index, { column: event.target.value })}>
          <option value="">选择列</option>{summary.column && !allowed.some((column) => column.name === summary.column) && <option value={summary.column}>当前列不可用：{summary.column}</option>}
          {allowed.map((column, i) => <option key={i} value={column.name}>{column.name}</option>)}</select></label>
        {summary.operation === "average" && <label>小数位<input aria-label={`第 ${index + 1} 项平均小数位`} type="number" min={0} max={8} step={1} value={summary.decimals} onChange={(event) => patchSummary(index, { decimals: Number(event.target.value) })} /></label>}
        <button type="button" onClick={() => onChange({ ...value, summaries: value.summaries.filter((_, i) => i !== index) })}>删除此汇总</button>
      </div>;
    })}
    <Button type="button" variant="secondary" disabled={!spec || value.summaries.length >= 20} onClick={() => onChange({ ...value,
      summaries: [...value.summaries, { column: "", operation: "count", decimals: 2 }] })}>添加汇总</Button>
    <label className="block"><input type="checkbox" checked={!!value.chart} onChange={(event) => onChange({ ...value,
      chart: event.target.checked ? { kind: "bar", category_column: "", value_column: "", title: "数据图表" } : null })} />生成图表附件</label>
    {value.chart && <div className="space-y-1">
      <p>图表仅引用原始数据行，需要至少一行；类别列为文本或数字，数值列为数字。附件预览不显示图表，请下载后查看；业务和视觉未验收。</p>
      <p>图表不能关联 run，创建不是幂等操作。结果不明时请先检查项目附件，再决定是否重试。</p>
      <label>图表类型<select aria-label="图表类型" value={value.chart.kind} onChange={(event) => patchChart({ kind: event.target.value as OfficeChart["kind"] })}><option value="bar">柱状图</option><option value="line">折线图</option></select></label>{" "}
      <label>类别列<select aria-label="图表类别列" value={value.chart.category_column} onChange={(event) => patchChart({ category_column: event.target.value })}>
        <option value="">选择列</option>{value.chart.category_column && !categories.some((column) => column.name === value.chart!.category_column) && <option value={value.chart.category_column}>当前列不可用：{value.chart.category_column}</option>}
        {categories.map((column, i) => <option key={i} value={column.name}>{column.name}</option>)}</select></label>{" "}
      <label>数值列<select aria-label="图表数值列" value={value.chart.value_column} onChange={(event) => patchChart({ value_column: event.target.value })}>
        <option value="">选择列</option>{value.chart.value_column && !numeric.some((column) => column.name === value.chart!.value_column) && <option value={value.chart.value_column}>当前列不可用：{value.chart.value_column}</option>}
        {numeric.map((column, i) => <option key={i} value={column.name}>{column.name}</option>)}</select></label>{" "}
      <label>标题<input aria-label="图表标题" value={value.chart.title} maxLength={100} onChange={(event) => patchChart({ title: event.target.value })} /></label>
    </div>}
  </details>;
}
