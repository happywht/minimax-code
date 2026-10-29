import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button, Input, Textarea } from "../../ui";
import type { AttachmentParserStatus, DocxSection, ReportSource, ResearchSource } from "../../types/ipc";
import { PdfOriginalPreview } from "./PdfOriginalPreview";
import { XlsxTablePreview } from "./XlsxTablePreview";
import { ReportSectionsEditor, readEditableSections } from "./ReportSectionsEditor";
import { ReportResearchPicker } from "./ReportResearchPicker";
import { DraftFileControl } from "./DraftFileControl";

const sectionExample = JSON.stringify([{ heading: "研究结论", blocks: [
  { kind: "paragraph", text: "在此填写正文及来源。" },
  { kind: "table", rows: [["项目", "结果"], ["示例", "待核实"]] },
] }], null, 2);

const labels: Record<AttachmentParserStatus, string> = {
  parsed: "已解析", empty: "空文本", truncated: "预览已截断", parse_failed: "解析失败",
  type_mismatch: "类型不匹配", parser_unavailable: "解析器不可用", ocr_not_attempted: "未尝试 OCR",
};
export type ReportDraft = { docName: string; title: string; body: string; format: "docx" | "pdf" | "both";
  structured: boolean; sections: string; visualSections: boolean; sources: ReportSource[]; researchIds: string[] };
function readReportDraft(value: unknown): ReportDraft {
  if (!value || typeof value !== "object") throw new Error("报告草稿格式无效");
  const data = value as Record<string, unknown>;
  if (typeof data.docName !== "string" || typeof data.title !== "string" || typeof data.body !== "string" || typeof data.sections !== "string" ||
      !["docx", "pdf", "both"].includes(String(data.format)) || typeof data.structured !== "boolean" || typeof data.visualSections !== "boolean" ||
      !Array.isArray(data.sources) || !Array.isArray(data.researchIds) || data.sources.length + data.researchIds.length > 30 ||
      data.researchIds.some((id) => typeof id !== "string" || id.length > 200) || new Set(data.researchIds).size !== data.researchIds.length ||
      data.sources.some((source) => !source || typeof source !== "object" || typeof source.title !== "string" || typeof source.url !== "string" ||
        (source.accessed_at !== undefined && typeof source.accessed_at !== "string"))) throw new Error("报告草稿字段无效");
  if (data.visualSections && (!data.structured || !readEditableSections(data.sections as string))) throw new Error("可视章节无法恢复；请修正草稿内容");
  return data as ReportDraft;
}

// Only simple numeric markers are recognized; this is a navigation aid, not validation.
function reportMarkers(body: string, sections: string, structured: boolean): number[] | null {
  let text = body;
  if (structured) {
    try {
      const value: unknown = JSON.parse(sections);
      if (!Array.isArray(value)) return null;
      const strings: string[] = [];
      const collect = (item: unknown): void => {
        if (typeof item === "string") strings.push(item);
        else if (Array.isArray(item)) item.forEach(collect);
        else if (item && typeof item === "object") Object.values(item).forEach(collect);
      };
      collect(value); text = strings.join("\n");
    } catch { return null; }
  }
  return [...new Set(Array.from(text.matchAll(/\[([1-9][0-9]*)\]/g), (match) => Number(match[1])))].sort((a, b) => a - b);
}

export type RetainedReportDraft = Omit<ReportDraft, "researchIds"> & { researchNeedsReview: boolean; citationNeedsReview?: boolean; file: File | null };

export function AttachmentsPanel({ onClose, embedded = false, initialDraft, onRetainDraft }: { onClose: () => void; embedded?: boolean; initialDraft?: RetainedReportDraft; onRetainDraft?: (draft: RetainedReportDraft) => void }): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.projects.find((item) => item.id === projectId));
  const state = useAttachmentStore(), host = ipc.agentBaseUrl;
  const scoped = state.projectId === projectId && state.host === host;
  const [file, setFile] = useState<File | null>(initialDraft?.file ?? null);
  const [addingContext, setAddingContext] = useState(false);
  const [includeExcerpt, setIncludeExcerpt] = useState(false);
  const [contextNotice, setContextNotice] = useState("");
  const [contextAdded, setContextAdded] = useState(false);
  const contextEpoch = useRef(0);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const filteredAttachments = (scoped ? state.attachments : []).filter((row) => `${row.original_name} ${row.id}`.toLowerCase().includes(query.trim().toLowerCase()) && (typeFilter === "all" || row.recognized_type === typeFilter));
  const [docName, setDocName] = useState(initialDraft?.docName ?? "报告.docx"), [title, setTitle] = useState(initialDraft?.title ?? "");
  const [body, setBody] = useState(initialDraft?.body ?? "");
  const [format, setFormat] = useState<"docx" | "pdf" | "both">(initialDraft?.format ?? "docx");
  const [structured, setStructured] = useState(initialDraft?.structured ?? false), [sections, setSections] = useState(initialDraft?.sections ?? sectionExample);
  const [visualSections, setVisualSections] = useState(initialDraft?.visualSections ?? false);
  const [sources, setSources] = useState<ReportSource[]>(initialDraft?.sources ?? []);
  const [researchSources, setResearchSources] = useState<ResearchSource[]>([]);
  const [researchNeedsReview, setResearchNeedsReview] = useState(initialDraft?.researchNeedsReview ?? false);
  const [citationNeedsReview, setCitationNeedsReview] = useState(initialDraft?.citationNeedsReview ?? false);
  const markers = reportMarkers(body, sections, structured);
  const noteSourceChange = () => { if (markers === null || markers.length) setCitationNeedsReview(true); };
  const draftRef = useRef<RetainedReportDraft>({ docName, title, body, format, structured, sections, visualSections, sources, researchNeedsReview, citationNeedsReview, file });
  draftRef.current = { docName, title, body, format, structured, sections, visualSections, sources, researchNeedsReview: researchNeedsReview || researchSources.length > 0, citationNeedsReview, file };
  useEffect(() => () => { onRetainDraft?.(draftRef.current); }, []);
  const importedSources = useRef<ResearchSource[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [submittedPair, setSubmittedPair] = useState<string | null>(null);
  const pairFingerprint = JSON.stringify([docName, title, structured, structured ? sections : body, sources, researchSources.map((item) => item.page_source_id), sessionId]);
  useEffect(() => {
    setQuery(""); setTypeFilter("all"); setFormError(null); setSubmittedPair(null); void useAttachmentStore.getState().activate(projectId);
    return () => useAttachmentStore.getState().clear();
  }, [projectId, host]);
  useEffect(() => {
    if (researchSources.length) setResearchNeedsReview(true);
    setResearchSources([]);
  }, [projectId, sessionId, host, state.serviceId]);
  useEffect(() => {
    ++contextEpoch.current; setContextNotice(""); setContextAdded(false); setAddingContext(false);
  }, [projectId, sessionId, host, state.serviceId, state.selected?.id]);
  useEffect(() => ipc.onSideCar(() => { ++contextEpoch.current; setContextNotice(""); setContextAdded(false); setAddingContext(false); }), []);
  const addAttachmentContext = async () => {
    const selected = state.selected, service = state.serviceId;
    if (!scoped || !projectId || !sessionId || !service || !selected || addingContext) return;
    const ticket = ++contextEpoch.current, id = selected.id, hash = selected.sha256;
    const current = () => {
      const session = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === contextEpoch.current && ipc.agentBaseUrl === host && session.currentProjectId === projectId &&
        session.currentSessionId === sessionId && attachments.projectId === projectId && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === hash;
    };
    setAddingContext(true); setContextNotice(""); setContextAdded(false);
    try {
      const checkScope = async () => {
        const [identity, session] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(sessionId)]);
        if (!current()) return false;
        if (identity.service_id !== service || session.session.id !== sessionId || session.session.project_id !== projectId)
          throw new Error("会话项目或服务归属已变化，请刷新并重新选择附件");
        return true;
      };
      if (!await checkScope()) return;
      const result = await typedIPC.getAttachment(projectId, id);
      if (!current()) return;
      const row = result.attachment;
      if (row.id !== id || row.project_id !== projectId || row.service_id !== service || row.sha256 !== hash ||
          row.version !== selected.version || row.original_name !== selected.original_name ||
          row.parser_status !== selected.parser_status || row.parser_scope !== selected.parser_scope ||
          row.preview_truncated !== selected.preview_truncated || typeof result.preview_text !== "string" ||
          new TextEncoder().encode(result.preview_text).length > 65536) throw new Error("附件身份或解析状态已变化，请重新选择");
      const supportsPreview = ["parsed", "empty", "truncated"].includes(row.parser_status);
      const preview = supportsPreview ? Array.from(result.preview_text) : [];
      const clipped = preview.length > 1200 || row.preview_truncated || row.parser_status === "truncated";
      const details = [includeExcerpt ? "项目附件（以下元数据及摘录加入草稿，尚未发送；内容未经核验）：" : "项目附件引用（仅元数据加入草稿，尚未发送；未读取原件）：",
        `文件名：${row.original_name} · 版本：${row.version}`, `附件 ID：${row.id}`, `SHA-256：${row.sha256}`,
        `声明类型：${row.declared_type} · 识别类型：${row.recognized_type}`,
        `解析状态：${labels[row.parser_status]} · 范围：${row.parser_scope} · 原因：${row.parser_reason}`,
        ...(includeExcerpt ? [
          `文本预览${clipped ? "（已截断，不代表原文件全文）" : "（有界解析内容，不保证覆盖原文件全文）"}：`,
          supportsPreview ? (preview.slice(0, 1200).join("") || "（没有可用的提取文本）") : "（解析不可用，未提供文本摘录）",
        ] : []),
        "如当前运行环境支持项目附件读取工具，可按附件 ID 进一步读取其支持的内容；请区分原件、解析预览和未验证事实。"].join("\n");
      if (!await checkScope()) return;
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text: details };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      if (!current()) return;
      setContextNotice(detail.result ?? "当前聊天输入区未打开；请先切换到聊天，再重新选择附件。");
      setContextAdded(detail.result === "已添加到当前会话草稿，尚未发送。");
    } catch (error) { if (current()) setContextNotice(`未添加附件：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (current()) setAddingContext(false); }
  };
  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="项目附件">
    <div className="flex items-center justify-between">
      <h2 className="text-lg font-semibold">项目附件 · {project?.name ?? "选择项目"}</h2>
      {!embedded && <Button variant="ghost" onClick={onClose}>关闭</Button>}
    </div>
    <p className="text-sm text-ink-1">最大 10 MiB。每次上传保留独立原件；同名文件创建新版本。</p>
    <p className="text-sm text-ink-1">TXT / CSV 严格 UTF-8 预览，最多 64 KiB / 2000 行；CSV 公式只作文本。可选 DOCX 解析仅包含正文段落和表格，保留位置标签；不含页眉页脚、修订、文本框、批注、脚注或图片文字。可选 PDF 解析按物理页序提取已有文字，最多 200 页，不保证版式或表格结构；扫描图片未尝试 OCR。可选 XLSX 解析只预览有界单元格、公式文本和未验证缓存，不计算公式，不执行宏或访问外链。</p>
    <p className="text-sm text-ink-1">原件保存、可解析、质量验证与交付是不同状态；当前质量均为未验证。附件不会自动加入对话上下文。</p>
    {ipc.isMock && <p role="status">演示模式：原件仅保存在本页内存，刷新后清空；Office 容器只标记候选 ZIP。PDF / DOCX / XLSX 解析需要真实后端可选依赖。</p>}
    <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); if (file) void state.upload(file); }}>
      <input aria-label="选择附件原件" type="file" disabled={state.busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); event.target.value = ""; }} />
      {file && <span className="break-all text-xs text-ink-1">待上传：{file.name}（{file.size} 字节）</span>}
      {file && <Button type="button" variant="secondary" disabled={state.busy} onClick={() => setFile(null)}>清除待上传文件</Button>}
      <Button type="submit" disabled={!scoped || !file || file.size > 10485760 || !state.serviceId || state.busy || state.loading}>上传原件</Button>
      <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(projectId)}>刷新</Button>
    </form>
    {scoped && state.loading && <p role="status">正在读取附件…</p>}
    {scoped && !state.loading && !state.attachments.length && <p>当前项目还没有附件原件。</p>}
    <div className="space-y-2 rounded border border-line p-3"><h3 className="font-semibold">已有附件与报告</h3>
      <div className="flex flex-wrap gap-2"><Input aria-label="搜索附件" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按名称或附件 ID 筛选" />
      <select aria-label="筛选附件类型" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} className="rounded border border-line bg-surface-0 px-2 text-xs text-ink-1"><option value="all">全部类型</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="xlsx">XLSX</option><option value="csv">CSV</option><option value="txt">TXT</option></select></div>
      <p className="text-xs text-ink-2">筛选当前项目已加载记录；已选预览不会因筛选而改变。匹配 {filteredAttachments.length} 项。</p>
    <ul className="space-y-1">{filteredAttachments.map((row) => <li key={row.id}>
      <button disabled={state.busy} className="max-w-full break-all rounded border border-line p-2 text-left hover:bg-surface-3" onClick={() => void state.select(row.id)}>
        {row.original_name} · v{row.version} · {row.id} · {row.size_bytes} 字节 · {labels[row.parser_status]} / 未验证
      </button>
    </li>)}</ul>
    {scoped && state.selected && <div className="space-y-2 rounded border border-line p-3">
      <h3 className="font-medium">已选附件 · {state.selected.original_name}</h3>
      <p className="break-all text-xs text-ink-1">声明类型: {state.selected.declared_type} · 识别候选: {state.selected.recognized_type} · {labels[state.selected.parser_status]} · 未验证</p>
      <p className="break-all text-xs text-ink-1">解析算法 v{state.selected.parser_version} · 范围 {state.selected.parser_scope} · 原因 {state.selected.parser_reason}</p>
      {state.selected.parser_reason === "no_extracted_body_text_no_ocr" && <p role="status">{state.selected.recognized_type === "pdf" ? "未提取到 PDF 页面文本；未尝试 OCR。原件可能仍包含其他内容。" : "未提取到正文文本；未尝试 OCR。原件可能仍包含其他内容。"}</p>}
      {state.selected.recognized_type === "pdf" && <p role="status">预览只包含已有文字；页码为物理页序号，空页标记不代表文件没有内容，未尝试 OCR。</p>}
      {state.selected.recognized_type === "xlsx" && <p role="status">XLSX 仅预览存储的单元格、坐标和类型；公式与缓存值分别标注，不执行或重算公式，缓存可能缺失或陈旧，未验证正确性。每表最多 A1:T100，全局最多 1500 个单元格；不还原 Excel 排版。含外部关系（包括超链接）的文件不支持解析。</p>}
      {state.selected.recognized_type === "xlsx" && state.selected.parser_status === "empty" && <p role="status">有界扫描未发现非空单元格；不等于文件没有其他内容。</p>}
      {state.selected.preview_truncated && <p role="status">{state.selected.recognized_type === "xlsx" ? "预览已达到单元格窗口、数量或 64 KiB / 2000 行展示上限，部分存储内容未显示；可下载完整原件。" : "预览已达到 64 KiB / 2000 行展示上限，后续内容未显示；可下载完整原件。"}</p>}
      <p className="break-all text-xs text-ink-1">SHA-256: {state.selected.sha256}</p>
      <label className="flex items-center gap-2 text-xs text-ink-1"><input type="checkbox" checked={includeExcerpt} disabled={addingContext} onChange={(event) => setIncludeExcerpt(event.target.checked)} />同时加入最多 1200 字解析摘录</label>
      <div className="flex flex-wrap gap-2"><Button type="button" disabled={state.busy || state.loading || addingContext || !sessionId || !state.serviceId} loading={addingContext} onClick={() => void addAttachmentContext()}>加入当前会话草稿</Button>
      <Button disabled={state.busy} onClick={() => void state.download()}>下载附件原始字节</Button>
      {contextAdded && <Button type="button" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}</div>
      <p className="text-xs text-ink-2">默认仅追加附件身份；可选择加入解析摘录。不会发送或上传原件到对话，也不代表模型已读取完整文件。</p>
      {contextNotice && <p role="status" className="text-xs text-ink-1">{contextNotice}</p>
      <PdfOriginalPreview attachment={state.selected} />
      <XlsxTablePreview key={`${state.selected.id}:${state.selected.sha256}`} attachment={state.selected} preview={state.preview} />
      {["parsed", "empty", "truncated"].includes(state.selected.parser_status)
        ? <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3" aria-label="附件纯文本预览">{state.preview}</pre>
        : <p role="status">当前状态不提供文本预览；可下载保存的原件。</p>}
    </div>}
    </div>
    <form className="flex flex-col gap-2 rounded border border-line p-3" onSubmit={(event) => {
      event.preventDefault(); setFormError(null);
      if (researchNeedsReview || citationNeedsReview) { setFormError("请先核对研究来源和正文引用编号。"); return; }
      if (sources.some((source) => !source.title.trim() || !source.url.trim())) {
        setFormError("请填写每条来源的标题和网址，或删除空来源。"); return;
      }
      if (sources.length + researchSources.length > 30 || (researchSources.length && !sessionId)) {
        setFormError("来源总数最多 30 条，研究来源需要当前项目会话。"); return;
      }
      const references = {
        ...(sources.length ? { sources: sources.map((source) => ({
          title: source.title.trim(), url: source.url.trim(),
          ...(source.accessed_at?.trim() ? { accessed_at: source.accessed_at.trim() } : {}),
        })) } : {}),
        ...(researchSources.length && sessionId ? { session_id: sessionId, page_source_ids: researchSources.map((item) => item.page_source_id) } : {}),
      };
      if (!structured) {
        const content = { paragraphs: body.split(/\r?\n/), ...references };
        if (format === "both") { setSubmittedPair(pairFingerprint); void state.generatePair(docName, title, content); }
        else void state.generateDocx(docName, title, content, format);
        return;
      }
      try {
        const value: unknown = JSON.parse(sections);
        if (!Array.isArray(value)) throw new Error("章节必须是 JSON 数组");
        const content = { sections: value as DocxSection[], ...references };
        if (format === "both") { setSubmittedPair(pairFingerprint); void state.generatePair(docName, title, content); }
        else void state.generateDocx(docName, title, content, format);
      } catch { setFormError("章节 JSON 无效，请使用示例格式。"); }
    }}>
      <h3>生成报告</h3>
      <DraftFileControl<ReportDraft> kind="report" disabled={state.busy || state.loading}
        scope={scoped && projectId && state.serviceId ? { host, service: state.serviceId, project: projectId, session: sessionId } : null}
        capture={() => ({ docName, title, body, format, structured, sections, visualSections, sources, researchIds: researchSources.map((item) => item.page_source_id) })}
        decode={async (value, scope) => {
          const draft = readReportDraft(value);
          if (draft.researchIds.length && !scope.session) throw new Error("研究来源需要原会话");
          const resolved: ResearchSource[] = [];
          for (const id of draft.researchIds) {
            const result = await typedIPC.getResearchSource({ project_id: scope.project, session_id: scope.session!, expected_service_id: scope.service, page_source_id: id });
            if (result.project_id !== scope.project || result.session_id !== scope.session || result.service_id !== scope.service || result.source.page_source_id !== id)
              throw new Error("研究来源归属已变化，请重新选择");
            resolved.push(result.source);
          }
          importedSources.current = resolved;
          return draft;
        }}
        apply={(draft) => {
          setDocName(draft.docName); setTitle(draft.title); setBody(draft.body); setFormat(draft.format); setStructured(draft.structured);
          setSections(draft.sections); setVisualSections(draft.visualSections); setSources(draft.sources); setFormError(null);
          setResearchSources(importedSources.current); setResearchNeedsReview(false);
          const restoredMarkers = reportMarkers(draft.body, draft.sections, draft.structured);
          setCitationNeedsReview(restoredMarkers === null || restoredMarkers.length > 0);
        }} />
      <p>填写标题与正文（每行一个段落），可粘贴已核对的资料和来源。生成独立新文件，不修改附件原件；不进行版式渲染或事实核验。</p>
      <label>文件格式 <select aria-label="报告文件格式" value={format} onChange={(event) => {
        const next = event.target.value as "docx" | "pdf" | "both"; setFormat(next);
        setDocName((name) => `${name.replace(/\.(docx|pdf)$/i, "")}${next === "both" ? "" : `.${next}`}`);
      }}><option value="docx">DOCX</option><option value="pdf">PDF</option><option value="both">DOCX + PDF</option></select></label>
      {format === "both" && <p>文件名填写不带扩展名的公共名称。逐项保存；同一输入已有结果后，请选择单一格式重试或修改输入以创建新版本。不确定结果时先刷新附件。</p>}
      {format !== "docx" && <p>PDF 需要已安装的中文 TrueType 字体；缺失或字符不受支持时请按提示配置。表格最多 10 列、每表 1000 格。</p>}
      <Input aria-label="报告文件名" value={docName} onChange={(event) => setDocName(event.target.value)} />
      <Input aria-label="报告标题" value={title} onChange={(event) => setTitle(event.target.value)} />
      <label>内容形式 <select aria-label="报告内容形式" value={!structured ? "plain" : visualSections ? "visual" : "sections"} onChange={(event) => {
        const next = event.target.value; setFormError(null);
        if (next === "visual" && !readEditableSections(sections)) { setFormError("当前 JSON 无法无损显示为可视编辑，请先修正 JSON。原内容已保留。"); return; }
        setStructured(next !== "plain"); setVisualSections(next === "visual");
      }}>
        <option value="plain">普通段落</option><option value="visual">章节可视编辑</option><option value="sections">章节和表格 JSON</option>
      </select></label>
      {structured ? visualSections ? (() => {
        const editable = readEditableSections(sections);
        return editable ? <ReportSectionsEditor value={editable} onChange={(value) => setSections(JSON.stringify(value, null, 2))} /> : <p role="alert">章节内容无法显示，请切换 JSON 查看。</p>;
      })() : <><p>按示例填写章节标题与依次排列的段落、表格；表格每行列数必须一致，单元格均为文本。</p>
        <Textarea aria-label="章节和表格 JSON" rows={12} value={sections} onChange={(event) => setSections(event.target.value)} /></> :
        <Textarea aria-label="报告正文（每行一个段落）" rows={6} value={body} onChange={(event) => setBody(event.target.value)} />}
      <div className="space-y-2">
        <p>参考来源（可选，最多 30 条；正文可用 [1]、[2] 对应来源顺序。只保存提供的信息，不自动核验）</p>
        {sources.map((source, index) => <div key={index} className="space-y-1 rounded border border-line p-2">
          <Input aria-label={`来源 ${index + 1} 标题`} placeholder="来源标题" value={source.title} onChange={(event) => { noteSourceChange(); setSources((current) => current.map((item, i) => i === index ? { ...item, title: event.target.value } : item)); }} />
          <Input aria-label={`来源 ${index + 1} 网址`} placeholder="https://example.com" value={source.url} onChange={(event) => { noteSourceChange(); setSources((current) => current.map((item, i) => i === index ? { ...item, url: event.target.value } : item)); }} />
          <Input aria-label={`来源 ${index + 1} 访问时间`} placeholder="可选：2026-10-29 或带时区的 ISO 时间" value={source.accessed_at ?? ""} onChange={(event) => setSources((current) => current.map((item, i) => i === index ? { ...item, accessed_at: event.target.value } : item))} />
          <Button type="button" variant="secondary" onClick={() => { noteSourceChange(); setSources((current) => current.filter((_, i) => i !== index)); }}>删除来源 {index + 1}</Button>
        </div>)}
        <Button type="button" variant="secondary" disabled={sources.length + researchSources.length >= 30} onClick={() => { noteSourceChange(); setSources((current) => [...current, { title: "", url: "" }]); }}>添加来源</Button>
      </div>
      {researchNeedsReview && <div role="alert" className="rounded border border-line p-2">
        <p>恢复的草稿未保留所选研究来源。请重新选择所需来源，并核对正文中的引用编号；来源记录可能已变化或删除。</p>
        <Button type="button" variant="secondary" onClick={() => setResearchNeedsReview(false)}>已核对研究来源和引用编号</Button>
      </div>}
      <ReportResearchPicker project={projectId} session={sessionId} service={scoped ? state.serviceId : null}
        selected={researchSources} onChange={(items) => { noteSourceChange(); setResearchSources(items); }} manualCount={sources.length} />
      <div className="space-y-2 rounded border border-line p-2" aria-label="正文来源编号对照">
        <p>正文编号对照：仅识别形如 [1] 的独立数字标记；其他引用写法无法检查。请自行核对每处对应关系与事实。</p>
        {markers === null ? <p role="status">章节 JSON 无法读取，不能检查编号；原草稿未更改。</p> : <>
          <p>识别到的编号：{markers.length ? markers.map((number) => `[${number}]`).join("、") : "无"}。</p>
          {markers.some((number) => number > sources.length + researchSources.length) && <p role="alert">有编号超出当前来源列表，请检查正文和来源。</p>}
        </>}
        <ol>{sources.map((source, index) => <li key={`manual-${index}`}>[{index + 1}] 手工来源：{source.title || "（未填写标题）"} · {source.url || "（未填写网址）"}</li>)}
          {researchSources.map((source, index) => <li key={source.page_source_id}>[{sources.length + index + 1}] 研究来源：{source.title || "（页面未提供标题）"} · {source.url} · {source.page_source_id}</li>)}</ol>
        {citationNeedsReview && <div role="alert"><p>来源或顺序已变化，或草稿已恢复；正文编号不会自动更新。请逐处核对后继续。</p>
          <Button type="button" variant="secondary" onClick={() => setCitationNeedsReview(false)}>已核对正文编号和来源顺序</Button></div>}
      </div>
      {formError && <p role="alert">{formError}</p>}
      <Button type="submit" disabled={researchNeedsReview || citationNeedsReview || !scoped || !state.serviceId || state.busy || state.loading || ipc.isMock || !title.trim() || !(structured ? sections.trim() : body.trim()) || (format === "both" && !!state.reportBatch && submittedPair === pairFingerprint)}>生成 {format === "both" ? "DOCX + PDF" : format.toUpperCase()}</Button>
      {ipc.isMock && <p role="status">演示模式不能生成报告。</p>}
    </form>
    {scoped && state.reportBatch && <div role="status" className="rounded border border-line p-2">
      <p>{state.reportBatch.status === "complete" ? "请求的格式均已保存" : "并非所有格式都已确认保存"}</p>
      <ul>{state.reportBatch.results.map((item) => <li key={item.format}>{item.format.toUpperCase()}：{item.status === "saved"
        ? `已保存 ${item.attachment.original_name} · ${item.attachment.id}` : `未确认：${item.error}`}</li>)}</ul>
      {state.reportBatch.status !== "complete" && <p>先刷新附件核对未确认格式；确认缺失后选择该单一格式重试，避免重复版本。</p>}
    </div>}
    {file && file.size > 10485760 && <p role="alert">文件超过 10 MiB，未上传。</p>}
    {scoped && state.error && <p role="alert" className="text-status-error">{state.error}</p>}

  </section>;
}
