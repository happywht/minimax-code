import { useEffect, useMemo, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeTableSpec } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";
import { XlsxTablePreview } from "./XlsxTablePreview";
import { csvToOfficeSpec, parseOfficeCsv, type CsvTable } from "./officeCsv";
import { CsvTableEditor } from "./CsvTableEditor";
import { OfficeAnalysisControls, type OfficeAnalysisOptions } from "./OfficeAnalysisControls";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { ProjectXlsxImport } from "./ProjectXlsxImport";

const example = JSON.stringify({ sheet_name: "清单", columns: [
  { name: "名称", kind: "text" }, { name: "数量", kind: "number" },
], rows: [["钢材", 12], ["=原样文本", 3]] }, null, 2);

function saveJson(value: unknown, name: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function OfficeDeliveryPanel({ onClose, onText, embedded = false }: { onClose: () => void; onText: () => void; embedded?: boolean }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId), host = ipc.agentBaseUrl;
  const session = useSessionStore((state) => state.currentSessionId);
  const state = useOfficeDeliveryStore();
  const [filename, setFilename] = useState("清单.xlsx"), [runId, setRunId] = useState("");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState(example), [key, setKey] = useState(() => crypto.randomUUID());
  const generationScope = useRef({ active: false });
  const [inputs, setInputs] = useState<Record<string, OfficeTableSpec>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<OfficeAnalysisOptions>({ summaries: [], chart: null });
  const [chartPending, setChartPending] = useState(false);
  const [chartMarkerReady, setChartMarkerReady] = useState(false);
  const [csv, setCsv] = useState("");
  const [csvTable, setCsvTable] = useState<CsvTable | null>(null);
  const [originalCsvTable, setOriginalCsvTable] = useState<CsvTable | null>(null);
  const [tableDirty, setTableDirty] = useState(false);
  const [kinds, setKinds] = useState<OfficeTableSpec["columns"][number]["kind"][]>([]);
  const csvRef = useRef(csv);
  const fileReader = useRef<FileReader | null>(null);
  const fileSequence = useRef(0);
  const [filePending, setFilePending] = useState(false);
  const [candidate, setCandidate] = useState<{ name: string; text: string; table: CsvTable } | null>(null);
  const scoped = state.projectId === project && state.host === host;
  const filteredEntries = (scoped ? state.entries : []).filter((entry) => `${entry.attachment.original_name} ${entry.delivery.id} ${entry.delivery.run_id ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const marker = project && state.serviceId && scoped ? `minimax:office-chart-pending:${host}:${project}:${state.serviceId}` : null;
  useEffect(() => {
    setChartMarkerReady(false);
    if (!marker) return;
    try { setChartPending(localStorage.getItem(marker) === "pending"); setChartMarkerReady(true); }
    catch { setChartPending(true); setFormError("无法保存图表待确认状态，不能发起图表生成。"); }
  }, [marker]);
  useEffect(() => {
    const scope = { active: true }; generationScope.current = scope;
    const sequenceRef = fileSequence, readerRef = fileReader;
    setQuery(""); setKey(crypto.randomUUID()); setFormError(null); setRunId(""); setInputs({}); setFilePending(false); setCandidate(null);
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
      if (analysis.summaries.length === 0 && !analysis.chart) {
        void state.generate({ filename, spec, run_id: runId || null, request_key: requestKey }).then((result) => {
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
        if (runId.trim()) throw new Error("图表附件暂不支持关联 run，请清空 Run ID");
        if (!analysisSpec.rows.length || !chart.title.trim() || !analysisSpec.columns.some((c) => c.name === chart.category_column && (c.kind === "text" || c.kind === "number")) ||
            !analysisSpec.columns.some((c) => c.name === chart.value_column && c.kind === "number")) throw new Error("图表需要数据行、标题、有效类别列和数字列");
        if (!marker || !chartMarkerReady || chartPending) throw new Error("请先确认图表上次提交结果或检查本地状态");
        let prior: string | null;
        try { prior = localStorage.getItem(marker); }
        catch { throw new Error("无法读取图表待确认状态，不能发起图表生成"); }
        if (prior === "pending") { setChartPending(true); throw new Error("上次图表结果未确认，请先检查项目附件"); }
        try { localStorage.setItem(marker, "pending"); setChartPending(true); }
        catch { throw new Error("无法保存图表待确认状态，不能发起图表生成"); }
      }
      void state.generateAnalysis({ filename, spec, run_id: runId || null, request_key: requestKey,
        summaries: analysis.summaries, chart }).then((result) => {
        if (!scope.active || !result) return;
        if (result.kind === "delivery") setInputs((previous) => ({ ...previous, [`${result.delivery.service_id}:${result.delivery.id}`]: result.materialized_spec }));
        if (result.kind === "attachment" && marker) {
          try { localStorage.removeItem(marker); setChartPending(false); }
          catch { setFormError("图表已登记，但待确认标记无法清除；请检查项目附件。"); }
        }
      });
    } catch (error) { setFormError(error instanceof Error ? error.message : "表格 JSON 无效"); }
  };
  const allowChartRetry = async () => {
    if (!marker) return;
    const scope = generationScope.current;
    const accepted = await requestConfirmation({ title: "确认已检查项目附件？", description: "图表请求可能已经创建新附件；再次生成可能产生重复版本。", confirmLabel: "已检查，允许再次生成" });
    if (!accepted || !scope.active || ipc.agentBaseUrl !== host || useOfficeDeliveryStore.getState().projectId !== project ||
        useOfficeDeliveryStore.getState().serviceId !== state.serviceId) return;
    try { localStorage.removeItem(marker); setChartPending(false); setFormError(null); }
    catch { setFormError("无法清除待确认状态，请检查本地存储。"); }
  };
  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="办公成果">
    <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">办公成果 · 纯数据 XLSX</h2>
      {!embedded && <div className="flex gap-2"><Button variant="secondary" onClick={onText}>文本成果</Button><Button variant="ghost" onClick={onClose}>关闭</Button></div>}</div>
    <p className="break-all text-xs text-ink-1">当前项目 {project ?? "未选择"} · Agent {host} · 服务 {scoped ? state.serviceId : "刷新中"}</p>
    <p>仅新建单表 XLSX，不读取或覆盖原文件。1–20 列、最多 1000 数据行，含表头最多 5000 格；输入 1 MiB、文件 2 MiB。空串作为空白；数字最多 15 有效位，长编号请用 text。</p>
    <p>不生成或计算公式、宏、外链。可选静态汇总或图表；不做 PDF/OCR、版式、打印分页或业务核验，文本预览不还原 Excel 排版。</p>
    <p>在当前 Agent 服务登记不可变新文件；关联已有 run 只作来源引用，不改变其状态。切换页面只停止本页等待，已提交结果可刷新找回。</p>
    {ipc.isMock && <p role="status">演示模式不生成 Office 文件；请连接真实后端及 attachments 可选依赖。</p>}
    <div className="space-y-2 rounded border border-line p-3"><h3 className="font-semibold">已有表格成果</h3>
      <Input aria-label="搜索表格成果" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按文件名、成果 ID 或运行 ID 筛选" />
      <p className="text-xs text-ink-2">筛选当前项目已加载记录；已选预览不会因筛选而改变。匹配 {filteredEntries.length} 项。</p>
    <ul className="space-y-1">{filteredEntries.map((entry) => <li key={entry.delivery.id}><button className="w-full break-all rounded border border-line p-2 text-left hover:bg-surface-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" disabled={state.busy} onClick={() => void state.select(entry.delivery.id)}>
      {entry.attachment.original_name} · v{entry.attachment.version} · {entry.delivery.id} · 已登记 / 结构校验通过 · 业务未验证
    </button></li>)}</ul>
    {scoped && state.selected && <div className="space-y-2 rounded border border-line p-3">
      <h3 className="font-medium">已选表格 · {state.selected.attachment.original_name}</h3>
      <p>校验范围 {state.selected.delivery.validation_report.scope} · 全部 {state.selected.delivery.validation_report.cell_count} 格（含表头） · 未做版式或业务验收</p>
      <p>来源 run: {state.selected.delivery.run_id ?? "未关联 run"}</p>
      <p className="break-all text-xs">SHA-256: {state.selected.attachment.sha256}</p>
      <div className="flex flex-wrap gap-2"><Button disabled={state.busy} onClick={() => void state.download()}>下载 XLSX 原始字节</Button>
        <Button variant="secondary" onClick={() => saveJson(state.selected?.delivery.validation_report, "office-verification.json")}>下载校验报告</Button>
        <Button variant="secondary" disabled={state.busy || ipc.isMock || !!analysis.chart} onClick={() => { const next = crypto.randomUUID(); setKey(next); submit(next); }}>按当前输入生成同名新版本</Button></div>
      {inputs[`${state.selected.delivery.service_id}:${state.selected.delivery.id}`] ? <Button variant="secondary" onClick={() => saveJson(inputs[`${state.selected!.delivery.service_id}:${state.selected!.delivery.id}`], "office-input.json")}>下载此成果的输入 JSON</Button> :
        <p role="status">此成果的输入未保存在当前页面，请使用生成时另行保存的输入 JSON 复验；当前编辑器内容不一定匹配。</p>}
      <p>下载 XLSX 和对应输入 JSON 后可独立全值复验；校验报告不是数字签名。空串已按空白规范化。输入快照仅保留到离开页面或切换项目。</p>
      {state.selected.attachment.preview_truncated && <p role="status">预览已截断（A1:T100 / 1500 格 / 64 KiB）；全文件值比较已另行完成，可下载完整文件。</p>}
      <XlsxTablePreview key={`${state.selected.attachment.id}:${state.selected.attachment.sha256}`} attachment={state.selected.attachment} preview={state.selected.preview_text} />
      <pre className="whitespace-pre-wrap break-words rounded border border-line p-3" aria-label="办公成果纯文本预览">{state.selected.preview_text}</pre>
    </div>}
    </div>
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
        <Button type="button" variant="secondary" onClick={() => { try { setSource(JSON.stringify(csvToOfficeSpec(csvTable, kinds), null, 2)); edited(); } catch (error) { setFormError(error instanceof Error ? error.message : "转换失败"); } }}>转换为表格输入</Button>
      </div>}
    </details>
    <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); submit(key); }}>
      <Input aria-label="XLSX 文件名" value={filename} onChange={(event) => { setFilename(event.target.value); edited(); }} />
      <Input aria-label="关联已有 Run ID（可选）" value={runId} onChange={(event) => { setRunId(event.target.value); edited(); }} placeholder="可留空，仅支持本项目普通 native host run" />
      <Textarea aria-label="结构化表格 JSON" rows={12} value={source} onChange={(event) => { setSource(event.target.value); edited(); }} />
      <OfficeAnalysisControls spec={analysisSpec} value={analysis} onChange={(next) => { setAnalysis(next); edited(); }} />
      {analysis.chart && chartPending && <div role="status"><p>上次图表结果未确认，请先检查当前项目附件，重复提交可能创建新版本。</p>
        <Button type="button" variant="secondary" onClick={() => void allowChartRetry()}>已检查附件，允许再次生成</Button></div>}
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={!scoped || !state.serviceId || state.busy || ipc.isMock || (!!analysis.chart && (!chartMarkerReady || chartPending))}>生成并登记 XLSX</Button>
        <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(project)}>刷新</Button>
        <Button type="button" variant="secondary" onClick={() => { try { saveJson(parsed(), "office-input.json"); } catch { setFormError("请先输入有效表格 JSON"); } }}>保存当前输入 JSON</Button></div>
    </form>
    {(formError || scoped && state.error) && <p role="alert" className="text-status-error">{formError || state.error}</p>}
    {state.busy && <p role="status">正在生成、核对或读取成果…</p>}
    {scoped && state.chart && <div className="space-y-2"><p>图表已登记到项目附件：{state.chart.attachment.original_name} · {state.chart.attachment.id}</p>
      <p>校验范围 {state.chart.validation_report.scope}：已核对存储单元格和图表系列引用；未做图表视觉或业务验收。图表只引用原始数据行，汇总值不会自动重算。此回执只保留在当前页面，可在离开前下载。</p>
      <div className="flex flex-wrap gap-2"><Button disabled={state.busy} onClick={() => void state.downloadChart()}>下载图表 XLSX</Button>
        <Button variant="secondary" onClick={() => saveJson(state.chart?.validation_report, "office-chart-verification.json")}>下载图表校验回执</Button>
        <Button variant="secondary" onClick={() => saveJson(state.chart?.analysis_spec, "office-chart-input.json")}>下载图表输入 JSON</Button></div></div>}

  </section>;
}
