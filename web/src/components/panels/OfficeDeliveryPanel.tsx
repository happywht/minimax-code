import { useEffect, useMemo, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeOperatorNotes, OfficeTableSpec } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";
import { XlsxTablePreview } from "./XlsxTablePreview";
import { csvToOfficeSpec, parseOfficeCsv, serializeOfficeCsv, type CsvTable } from "./officeCsv";
import { CsvTableEditor } from "./CsvTableEditor";
import { OfficeAnalysisControls, type OfficeAnalysisOptions } from "./OfficeAnalysisControls";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { ProjectXlsxImport } from "./ProjectXlsxImport";
import { DraftFileControl } from "./DraftFileControl";
import type { ReportTableTransfer } from "./DeliveryWorkspace";
import { OfficeFollowupDraft } from "./OfficeFollowupDraft";
import { OfficeDeliveryReuse } from "./OfficeDeliveryReuse";
import { OfficeReportTableTransfer } from "./OfficeReportTableTransfer";

const example = JSON.stringify({ sheet_name: "清单", columns: [
  { name: "名称", kind: "text" }, { name: "数量", kind: "number" },
], rows: [["钢材", 12], ["=原样文本", 3]] }, null, 2);

function saveJson(value: unknown, name: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function saveCsv(table: CsvTable): void {
  const url = URL.createObjectURL(new Blob([serializeOfficeCsv(table)], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "表格处理结果.csv"; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type TableDraft = { filename: string; runId: string; source: string; csv: string; csvTable: CsvTable | null;
  originalCsvTable: CsvTable | null; tableDirty: boolean; kinds: OfficeTableSpec["columns"][number]["kind"][];
  candidate: { name: string; text: string; table: CsvTable } | null; analysis: OfficeAnalysisOptions; operatorNotes?: OfficeOperatorNotes };
function isDraftTable(value: unknown): value is CsvTable {
  if (!value || typeof value !== "object") return false;
  const table = value as Record<string, unknown>;
  if (!Array.isArray(table.headers) || !Array.isArray(table.rows) || table.headers.length < 1 || table.headers.length > 20 || table.rows.length > 1000 ||
      (table.rows.length + 1) * table.headers.length > 5000 || table.headers.some((item) => typeof item !== "string" || item.length > 4096)) return false;
  const width = table.headers.length;
  return table.rows.every((row) => Array.isArray(row) && row.length === width && row.every((item: unknown) => typeof item === "string" && item.length <= 4096));
}
function readTableDraft(value: unknown): TableDraft {
  if (!value || typeof value !== "object") throw new Error("表格草稿格式无效");
  const data = value as Record<string, unknown>;
  if (typeof data.filename !== "string" || typeof data.runId !== "string" || typeof data.source !== "string" || typeof data.csv !== "string" ||
      typeof data.tableDirty !== "boolean" || !Array.isArray(data.kinds) || data.kinds.length > 20 ||
      data.kinds.some((kind) => !["text", "number", "boolean"].includes(kind)) ||
      (data.csvTable !== null && !isDraftTable(data.csvTable)) || (data.originalCsvTable !== null && !isDraftTable(data.originalCsvTable)) ||
      (data.tableDirty && (!data.csvTable || !data.originalCsvTable)) ||
      (data.csvTable && data.kinds.length !== (data.csvTable as CsvTable).headers.length)) throw new Error("表格草稿字段无效");
  if (data.candidate !== null) {
    const candidate = data.candidate as Record<string, unknown>;
    if (!candidate || typeof candidate !== "object" || typeof candidate.name !== "string" || typeof candidate.text !== "string" || !isDraftTable(candidate.table)) throw new Error("待选择的 CSV 草稿无效");
  }
  const analysis = data.analysis as Record<string, unknown>;
  if (data.operatorNotes !== undefined) {
    const notes = data.operatorNotes as Record<string, unknown>;
    if (!notes || typeof notes !== "object" || Object.keys(notes).some((key) => !["source", "method", "limitations"].includes(key)) ||
        ["source", "method", "limitations"].some((key) => typeof notes[key] !== "string" || [...(notes[key] as string)].length > 2000)) throw new Error("处理说明草稿无效");
  }
  if (!analysis || typeof analysis !== "object" || !Array.isArray(analysis.summaries) || analysis.summaries.length > 20 ||
      analysis.summaries.some((item) => !item || typeof item !== "object" || typeof item.column !== "string" ||
        !["sum", "count", "average"].includes(item.operation) || typeof item.decimals !== "number" || !Number.isFinite(item.decimals))) throw new Error("汇总草稿无效");
  if (analysis.chart !== null) {
    const chart = analysis.chart as Record<string, unknown>;
    if (!chart || typeof chart !== "object" || !["bar", "line"].includes(String(chart.kind)) || typeof chart.category_column !== "string" ||
        typeof chart.value_column !== "string" || typeof chart.title !== "string") throw new Error("图表草稿无效");
  }
  return data as TableDraft;
}

export function OfficeDeliveryPanel({ onClose, onText, embedded = false, initialDraft, onRetainDraft, tableTransferPending = false, onPrepareReportTable }: { onClose: () => void; onText: () => void; embedded?: boolean; initialDraft?: TableDraft; onRetainDraft?: (draft: TableDraft) => void; tableTransferPending?: boolean; onPrepareReportTable?: (value: ReportTableTransfer) => void }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId), host = ipc.agentBaseUrl;
  const session = useSessionStore((state) => state.currentSessionId);
  const state = useOfficeDeliveryStore();
  const [filename, setFilename] = useState(initialDraft?.filename ?? "清单.xlsx"), [runId, setRunId] = useState(initialDraft?.runId ?? "");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState(initialDraft?.source ?? example), [key, setKey] = useState(() => crypto.randomUUID());
  const generationScope = useRef({ active: false });
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<OfficeAnalysisOptions>(initialDraft?.analysis ?? { summaries: [], chart: null });
  const [operatorNotes, setOperatorNotes] = useState<OfficeOperatorNotes>(initialDraft?.operatorNotes ?? { source: "", method: "", limitations: "" });
  const [csv, setCsv] = useState(initialDraft?.csv ?? "");
  const [csvTable, setCsvTable] = useState<CsvTable | null>(initialDraft?.csvTable ?? null);
  const [originalCsvTable, setOriginalCsvTable] = useState<CsvTable | null>(initialDraft?.originalCsvTable ?? null);
  const [tableDirty, setTableDirty] = useState(initialDraft?.tableDirty ?? false);
  const [kinds, setKinds] = useState<OfficeTableSpec["columns"][number]["kind"][]>(initialDraft?.kinds ?? []);
  const csvRef = useRef(csv);
  const fileReader = useRef<FileReader | null>(null);
  const fileSequence = useRef(0);
  const [filePending, setFilePending] = useState(false);
  const [candidate, setCandidate] = useState<{ name: string; text: string; table: CsvTable } | null>(initialDraft?.candidate ?? null);
  const draftRef = useRef<TableDraft>({ filename, runId, source, csv, csvTable, originalCsvTable, tableDirty, kinds, candidate, analysis, operatorNotes });
  draftRef.current = { filename, runId, source, csv, csvTable, originalCsvTable, tableDirty, kinds, candidate, analysis, operatorNotes };
  useEffect(() => () => { onRetainDraft?.(draftRef.current); }, []);
  const scoped = state.projectId === project && state.host === host;
  const filteredEntries = scoped ? state.entries : [];
  useEffect(() => {
    const scope = { active: true }; generationScope.current = scope;
    const sequenceRef = fileSequence, readerRef = fileReader;
    setQuery(""); setKey(crypto.randomUUID()); setFormError(null); setInputs({}); setFilePending(false);
    void useOfficeDeliveryStore.getState().activate(project);
    return () => { scope.active = false; sequenceRef.current++; readerRef.current?.abort(); readerRef.current = null; useOfficeDeliveryStore.getState().clear(); };
  }, [project, host]);
  const cancelFileRead = () => { fileSequence.current++; fileReader.current?.abort(); fileReader.current = null; setFilePending(false); };
  const applyCsv = (text: string, table: CsvTable) => {
    csvRef.current = text; setCsv(text); setCsvTable(table); setOriginalCsvTable(table); setTableDirty(false); setKinds(table.headers.map(() => "text"));
    setCandidate(null); setFormError(null);
  };
  const readCsvFile = (file: File) => {
    cancelFileRead(); setCandidate(null); setFormError(null);
    if (file.size > 1048576) { setFormError("CSV 文件超过 1 MiB，草稿未更改"); return; }
    const sequence = fileSequence.current;
    const reader = new FileReader(); fileReader.current = reader; setFilePending(true);
    reader.onload = () => {
      if (sequence !== fileSequence.current) return;
      fileReader.current = null; setFilePending(false);
      try {
        if (!(reader.result instanceof ArrayBuffer) || reader.result.byteLength > 1048576) throw new Error("CSV 文件大小无效");
        let text: string;
        try { text = new TextDecoder("utf-8", { fatal: true }).decode(reader.result); }
        catch { throw new Error("文件不是有效的 UTF-8"); }
        const table = parseOfficeCsv(text);
        if (csvRef.current === "") applyCsv(text, table);
        else setCandidate({ name: file.name, text, table });
      } catch (error) { setFormError(`${error instanceof Error ? error.message : "读取失败"}，草稿未更改`); }
    };
    reader.onerror = () => {
      if (sequence !== fileSequence.current) return;
      fileReader.current = null; setFilePending(false); setFormError("读取 CSV 文件失败，草稿未更改");
    };
    try { reader.readAsArrayBuffer(file); }
    catch { fileReader.current = null; setFilePending(false); setFormError("读取 CSV 文件失败，草稿未更改"); }
  };
  const edited = () => { setKey(crypto.randomUUID()); setFormError(null); };
  const parsed = () => {
    if (new TextEncoder().encode(source).length > 1048576) throw new Error("结构化输入超过 1 MiB");
    const spec = JSON.parse(source) as OfficeTableSpec;
    if (!spec || typeof spec !== "object" || !Array.isArray(spec.columns) || !Array.isArray(spec.rows)) throw new Error("需要 sheet_name、columns、rows 的表格 JSON");
    return spec;
  };
  const analysisSpec = useMemo(() => {
    try {
      const spec = JSON.parse(source) as OfficeTableSpec;
      if (!spec || typeof spec !== "object" || !Array.isArray(spec.columns) || !Array.isArray(spec.rows) ||
          spec.columns.some((column) => !column || typeof column.name !== "string" || !["text", "number", "boolean"].includes(column.kind))) return null;
      return spec;
    } catch { return null; }
  }, [source]);
  const submit = (requestKey: string) => {
    try {
      const spec = parsed(), scope = generationScope.current; setFormError(null);
      const notes = Object.values(operatorNotes).some((value) => value.trim()) ? { operator_notes: operatorNotes } : {};
      if (analysis.summaries.length === 0 && !analysis.chart) {
        void state.generate({ filename, spec, run_id: runId || null, request_key: requestKey, ...notes }).then((result) => {
          if (result && scope.active) setInputs((previous) => ({ ...previous, [`${result.delivery.service_id}:${result.delivery.id}`]: spec }));
        });
        return;
      }
      if (!analysisSpec) throw new Error("请先输入有效的表格列和数据");
      for (const summary of analysis.summaries) {
        const column = analysisSpec.columns.find((item) => item.name === summary.column);
        if (!column || (summary.operation !== "count" && column.kind !== "number")) throw new Error("请选择适用于汇总操作的列");
        if (!Number.isInteger(summary.decimals) || summary.decimals < 0 || summary.decimals > 8) throw new Error("平均小数位须为 0–8 的整数");
      }
      const chart = analysis.chart;
      if (chart) {
        if (!analysisSpec.rows.length || !chart.title.trim() || !analysisSpec.columns.some((c) => c.name === chart.category_column && (c.kind === "text" || c.kind === "number")) ||
            !analysisSpec.columns.some((c) => c.name === chart.value_column && c.kind === "number")) throw new Error("图表需要数据行、标题、有效类别列和数字列");

      }
      void state.generateAnalysis({ filename, spec, run_id: runId || null, request_key: requestKey,
        summaries: analysis.summaries, chart, ...notes }).then((result) => {
        if (!scope.active || !result) return;
        setInputs((previous) => ({ ...previous, [`${result.delivery.service_id}:${result.delivery.id}`]: result.analysis_spec ?? result.materialized_spec }));
      });
    } catch (error) { setFormError(error instanceof Error ? error.message : "表格 JSON 无效"); }
  };
  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="办公成果">
    <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">办公成果 · 纯数据 XLSX</h2>
      {!embedded && <div className="flex gap-2"><Button variant="secondary" onClick={onText}>文本成果</Button><Button variant="ghost" onClick={onClose}>关闭</Button></div>}</div>
    <p className="break-all text-xs text-ink-1">当前项目 {project ?? "未选择"} · Agent {host} · 服务 {scoped ? state.serviceId : "刷新中"}</p>
    <p>仅新建单表 XLSX，不读取或覆盖原文件。1–20 列、最多 1000 数据行，含表头最多 5000 格；输入 1 MiB、文件 2 MiB。空串作为空白；数字最多 15 有效位，长编号请用 text。</p>
    <p>不生成或计算公式、宏、外链。可选静态汇总或图表；不做 PDF/OCR、版式、打印分页或业务核验，文本预览不还原 Excel 排版。</p>
    <p>在当前 Agent 服务登记不可变新文件；关联已有 run 只作来源引用，不改变其状态。切换页面只停止本页等待，已提交结果可刷新找回。旧版生成的图表仍在项目附件中，不会自动补登记。</p>
    {ipc.isMock && <p role="status">演示模式不生成 Office 文件；请连接真实后端及 attachments 可选依赖。</p>}
    <div className="space-y-2 rounded border border-line p-3"><h3 className="font-semibold">已有表格成果</h3>
      <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void state.activate(project, query.trim()); }}>
        <Input aria-label="搜索表格成果" maxLength={240} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按文件名、成果 ID 或运行 ID 搜索" />
        <Button type="submit" variant="secondary" disabled={state.busy || state.paging}>搜索</Button>
      </form>
      <p className="text-xs text-ink-2">搜索整个项目，按字面区分大小写；已加载 {filteredEntries.length} 项。</p>
    <ul className="space-y-1">{filteredEntries.map((entry) => <li key={entry.delivery.id}><button className="w-full break-all rounded border border-line p-2 text-left hover:bg-surface-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" disabled={state.busy} onClick={() => void state.select(entry.delivery.id)}>
      {entry.attachment.original_name} · v{entry.attachment.version} · {entry.delivery.id} · 已登记 / {entry.delivery.validation_report.scope === "xlsx_chart_values_v1" ? "单元格及系列引用已核对" : "结构校验通过"} · 业务未验证
    </button></li>)}</ul>
    {scoped && state.nextCursor && <Button type="button" variant="secondary" disabled={state.busy || state.paging} onClick={() => void state.loadMore()}>{state.paging ? "正在加载…" : "加载更早成果"}</Button>}
    {scoped && state.selected && <div className="space-y-2 rounded border border-line p-3">
      <h3 className="font-medium">已选表格 · {state.selected.attachment.original_name}</h3>
      <p>校验范围 {state.selected.delivery.validation_report.scope} · 全部 {state.selected.delivery.validation_report.cell_count} 格（含表头） · {state.selected.delivery.validation_report.scope === "xlsx_chart_values_v1" ? "已核对单元格及图表系列引用" : "已核对纯表格结构"} · 未做版式或业务验收</p>
      <p>来源 run: {state.selected.delivery.run_id ?? "未关联 run"}</p>
      <p className="break-all text-xs">SHA-256: {state.selected.attachment.sha256}</p>
      {state.selected.delivery.validation_report.processing && <details><summary>表格处理说明</summary>
        <p>记录生成时的来源和处理参数，业务正确性未核验。筛选值和空值标记可能包含敏感内容。</p>
        <pre className="whitespace-pre-wrap break-words">{JSON.stringify(state.selected.delivery.validation_report.processing, null, 2)}</pre>
      </details>}
      {state.selected.delivery.validation_report.operator_notes && <details open><summary>用户填写的来源与处理说明（未核验）</summary>
        {([ ["输入来源", state.selected.delivery.validation_report.operator_notes.source], ["变换口径", state.selected.delivery.validation_report.operator_notes.method], ["限制与待核对事项", state.selected.delivery.validation_report.operator_notes.limitations] ] as const).map(([label, value]) => value && <div key={label}><p>{label}：</p><p className="whitespace-pre-wrap break-words">{value}</p></div>)}
      </details>}
      {state.selected.delivery.validation_report.scope === "xlsx_plain_cells_v1" && <OfficeFollowupDraft selected={state.selected} />}
      {state.selected.delivery.validation_report.scope === "xlsx_plain_cells_v1" && <OfficeDeliveryReuse key={`${state.selected.delivery.id}:${state.selected.attachment.sha256}`}
        selected={state.selected} project={project} session={session} service={state.serviceId} host={host}
        draftVersion={JSON.stringify([filename, runId, source, csv, csvTable, originalCsvTable, tableDirty, kinds, candidate, analysis, filePending])} filePending={filePending}
        onApply={(text, table, name) => { cancelFileRead(); applyCsv(text, table); setFilename(name); edited(); }} />}
      <div className="flex flex-wrap gap-2"><Button disabled={state.busy} onClick={() => void state.download()}>下载 XLSX 原始字节</Button>
        <Button variant="secondary" onClick={() => saveJson(state.selected?.delivery.validation_report, "office-verification.json")}>下载校验报告</Button>
        <Button variant="secondary" disabled={state.busy || ipc.isMock} onClick={() => { const next = crypto.randomUUID(); setKey(next); submit(next); }}>按当前文件名和输入生成新文件</Button></div>
      {inputs[`${state.selected.delivery.service_id}:${state.selected.delivery.id}`] ? <Button variant="secondary" onClick={() => saveJson(inputs[`${state.selected!.delivery.service_id}:${state.selected!.delivery.id}`], "office-input.json")}>下载此成果的输入 JSON</Button> :
        <p role="status">此成果的输入未保存在当前页面，请使用生成时另行保存的输入 JSON 复验；当前编辑器内容不一定匹配。</p>}
      <p>校验报告不是数字签名。空串已按空白规范化。输入快照仅保留到离开页面或切换项目；图表输入包含图表配置及原始数据行数。</p>
      {state.selected.attachment.preview_truncated && <p role="status">预览已截断（A1:T100 / 1500 格 / 64 KiB）；可下载完整文件。</p>}
      <XlsxTablePreview key={`${state.selected.attachment.id}:${state.selected.attachment.sha256}`} attachment={state.selected.attachment} preview={state.selected.preview_text} />
      <pre className="whitespace-pre-wrap break-words rounded border border-line p-3" aria-label="办公成果纯文本预览">{state.selected.preview_text}</pre>
    </div>}
    </div>
    <DraftFileControl<TableDraft> kind="table" disabled={state.busy || filePending}
      scope={scoped && project && state.serviceId ? { host, service: state.serviceId, project, session } : null}
      capture={() => ({ filename, runId, source, csv, csvTable, originalCsvTable, tableDirty, kinds, candidate, analysis, operatorNotes })}
      decode={async (value) => readTableDraft(value)}
      apply={(draft) => {
        cancelFileRead(); setFilename(draft.filename); setRunId(draft.runId); setSource(draft.source);
        csvRef.current = draft.csv; setCsv(draft.csv); setCsvTable(draft.csvTable); setOriginalCsvTable(draft.originalCsvTable);
        setTableDirty(draft.tableDirty); setKinds(draft.kinds); setCandidate(draft.candidate); setAnalysis(draft.analysis); setOperatorNotes(draft.operatorNotes ?? { source: "", method: "", limitations: "" }); edited();
      }} />
    <ProjectXlsxImport project={project} session={session} service={scoped ? state.serviceId : null} onImport={(text, table) => {
      const scope = generationScope.current, expectedService = state.serviceId;
      void (async () => {
        if (csvRef.current || tableDirty) {
          const accepted = await requestConfirmation({ title: "替换表格草稿？", description: "将替换当前 CSV 和表格编辑草稿；下方已转换的表格输入暂不改变。", confirmLabel: "替换草稿" });
          if (!accepted) return;
        }
        if (!scope.active || ipc.agentBaseUrl !== host || useSessionStore.getState().currentProjectId !== project ||
            useSessionStore.getState().currentSessionId !== session || useOfficeDeliveryStore.getState().serviceId !== expectedService) return;
        cancelFileRead(); applyCsv(text, table);
      })();
    }} />
    <details className="rounded border border-line p-3">
      <summary>从 CSV 创建表格输入</summary>
      <p>首行为列名，逗号分隔；支持引号内逗号和换行。默认全部为文本，保留前导零和公式样文字；空字符串变空白。转换会替换下方 JSON，源 CSV 不会被修改。</p>
      <label>选择本地 UTF-8 CSV 文件（最多 1 MiB）<input aria-label="选择本地 CSV 文件" type="file" accept=".csv,text/csv" onClick={cancelFileRead} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) readCsvFile(file); }} /></label>
      <p>文件仅在本页读取，不会上传或保存为项目附件。生成时提交转换后的表格数据。</p>
      {filePending && <p role="status">正在读取文件；现有草稿保留。</p>}
      {candidate && <div><p>已读取 {candidate.name}（{candidate.table.rows.length} 行）；当前 CSV 草稿不会自动覆盖。替换也会放弃尚未转换的表格编辑。</p><Button type="button" variant="secondary" onClick={() => applyCsv(candidate.text, candidate.table)}>使用此文件替换 CSV 草稿</Button><Button type="button" variant="ghost" onClick={() => setCandidate(null)}>保留当前草稿</Button></div>}
      <Textarea aria-label="粘贴 CSV" rows={6} value={csv} readOnly={tableDirty} onChange={(event) => { csvRef.current = event.target.value; setCsv(event.target.value); setCsvTable(null); setOriginalCsvTable(null); setFormError(null); }} />
      {tableDirty && <p role="status">表格有编辑，CSV 草稿暂为只读；恢复读取时的表格后可修改原稿。</p>}
      <Button type="button" variant="secondary" disabled={tableDirty} onClick={() => { try { const table = parseOfficeCsv(csv); setCsvTable(table); setOriginalCsvTable(table); setTableDirty(false); setKinds(table.headers.map(() => "text")); setFormError(null); } catch (error) { setCsvTable(null); setOriginalCsvTable(null); setFormError(error instanceof Error ? error.message : "CSV 无效"); } }}>读取 CSV 列</Button>
      {csvTable && <div className="space-y-2"><p>当前表格包含 {csvTable.rows.length} 行，{csvTable.headers.length} 列。数字须在支持的精度内；布尔只接受 true / false。</p>
        {csvTable.headers.map((name, index) => <label key={index} className="flex items-center gap-2">{name}<select aria-label={`第 ${index + 1} 列类型`} value={kinds[index]} onChange={(event) => setKinds((old) => old.map((kind, i) => i === index ? event.target.value as typeof kind : kind))}>
          <option value="text">文本</option><option value="number">数字</option><option value="boolean">布尔</option></select></label>)}
        {originalCsvTable && <CsvTableEditor table={csvTable} original={originalCsvTable} kinds={kinds} onChange={(next, nextKinds) => { setCsvTable(next); if (nextKinds) setKinds(nextKinds); setTableDirty(next !== originalCsvTable); setFormError(null); }} />}
        <p>下载的是当前表格的新文件，不修改原件。CSV 仅保存单元格文本，不保存列类型；表格软件可能将公式样文本解释为公式或自动转换数据。带类型 JSON 按所选列类型保存，空字符串转为空值，数字和布尔值会规范化。</p>
        <div className="flex flex-wrap gap-2"><Button type="button" variant="secondary" onClick={() => { saveCsv(csvTable); setFormError(null); }}>下载当前表格 CSV</Button>
          <Button type="button" variant="secondary" onClick={() => { try { saveJson(csvToOfficeSpec(csvTable, kinds), "表格处理结果.json"); setFormError(null); } catch (error) { setFormError(error instanceof Error ? error.message : "导出失败"); } }}>下载带类型表格 JSON</Button></div>
        <Button type="button" variant="secondary" onClick={() => { try { setSource(JSON.stringify(csvToOfficeSpec(csvTable, kinds), null, 2)); edited(); } catch (error) { setFormError(error instanceof Error ? error.message : "转换失败"); } }}>转换为表格输入</Button>
        {onPrepareReportTable && <OfficeReportTableTransfer table={csvTable} project={project} session={session} service={scoped ? state.serviceId : null} host={host} pending={tableTransferPending} onPrepare={onPrepareReportTable} />}
      </div>}
    </details>
    <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); submit(key); }}>
      <Input aria-label="XLSX 文件名" value={filename} onChange={(event) => { setFilename(event.target.value); edited(); }} />
      <Input aria-label="关联已有 Run ID（可选）" value={runId} onChange={(event) => { setRunId(event.target.value); edited(); }} placeholder="可留空，仅支持本项目普通 native host run" />
      <Textarea aria-label="结构化表格 JSON" rows={12} value={source} onChange={(event) => { setSource(event.target.value); edited(); }} />
      <OfficeAnalysisControls spec={analysisSpec} value={analysis} onChange={(next) => { setAnalysis(next); edited(); }} />
      <fieldset className="space-y-2 rounded border border-line p-2"><legend>成果处理说明（可选，用户填写）</legend>
        <p>说明会与成果回执一起保存并可下载；不会写入 XLSX，不自动追踪编辑或核验来源及业务结果。请在修改数据后更新，不要填写不必要的敏感信息。</p>
        {([ ["source", "输入来源", "文件、工作表、附件 ID 或数据来源"], ["method", "变换口径", "筛选、去重、分组、计算及其他处理步骤"], ["limitations", "限制与待核对事项", "例如未计算公式、缺失范围、业务核验状态"] ] as const).map(([key, label, placeholder]) => <label key={key} className="block">{label}
          <Textarea aria-label={`处理说明：${label}`} maxLength={2000} rows={3} value={operatorNotes[key]} placeholder={placeholder} onChange={(event) => { setOperatorNotes((old) => ({ ...old, [key]: event.target.value })); edited(); }} /></label>)}
      </fieldset>
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={!scoped || !state.serviceId || state.busy || ipc.isMock}>生成并登记 XLSX</Button>
        <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(project, state.query)}>刷新</Button>
        <Button type="button" variant="secondary" onClick={() => { try { saveJson(parsed(), "office-input.json"); } catch { setFormError("请先输入有效表格 JSON"); } }}>保存当前输入 JSON</Button></div>
    </form>
    {(formError || scoped && state.error) && <p role="alert" className="text-status-error">{formError || state.error}</p>}
    {state.busy && <p role="status">正在生成、核对或读取成果…</p>}


  </section>;
}
