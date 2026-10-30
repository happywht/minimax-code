import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import type { AgentRun, AgentRunStep, AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";
import { PdfOriginalPreview } from "../panels/PdfOriginalPreview";
import { XlsxTablePreview } from "../panels/XlsxTablePreview";
import { SearchableTextPreview } from "../panels/SearchableTextPreview";

export function RunAttachments({ run, steps, reviewScope, onPrepareAttachment, transferPending = false, verifyRun = false, search = "" }: { run: AgentRun; steps: AgentRunStep[]; reviewScope?: { sessionId: string; projectId: string; serviceId: string }; onPrepareAttachment?: (attachment: AttachmentOriginal, runId: string) => void; transferPending?: boolean; verifyRun?: boolean; search?: string }): JSX.Element | null {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const activeProject = useSessionStore((state) => state.currentProjectId) ?? "inbox";
  const session = reviewScope?.sessionId ?? activeSession;
  const project = reviewScope?.projectId ?? activeProject;
  const host = ipc.agentBaseUrl;
  const [selected, setSelected] = useState<AttachmentOriginal | null>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const version = useRef(0), download = useRef<AbortController | null>(null);
  const rows = steps.filter((step) => step.status === "completed" && step.run_id === run.id
    && step.session_id === run.session_id && step.payload?.source === "run_attachment_v1"
    && step.payload?.project_id === project && typeof step.payload?.attachment_id === "string"
    && typeof step.payload?.service_id === "string" && typeof step.payload?.sha256 === "string"
    && (!reviewScope || step.payload.service_id === reviewScope.serviceId));
  const needle = search.trim().toLocaleLowerCase();
  const matchingRows = rows.filter((step) => [step.payload?.filename, step.payload?.attachment_id].some((value) =>
    typeof value === "string" && value.toLocaleLowerCase().includes(needle)));
  const available = session === run.session_id && run.metadata?.project_id === project;
  const visible = selected && selected.project_id === project && (!reviewScope || selected.service_id === reviewScope.serviceId) ? selected : null;
  const current = (ticket: number, origin: string) => ticket === version.current && ipc.agentBaseUrl === origin
    && useSessionStore.getState().currentSessionId === activeSession
    && (useSessionStore.getState().currentProjectId ?? "inbox") === activeProject;
  useEffect(() => {
    const ref = version;
    ++ref.current; download.current?.abort(); setSelected(null); setPreview(""); setError(null); setLoading(false);
    return () => { ++ref.current; download.current?.abort(); };
  }, [run.id, session, project, host, activeSession, activeProject, reviewScope?.serviceId]);
  useEffect(() => ipc.onSideCar(() => {
    ++version.current; download.current?.abort(); setSelected(null); setPreview(""); setError(null); setLoading(false);
  }), []);

  const verifyAssociation = async (ticket: number, origin: string, id: string, hash: string, service: string) => {
    if (!verifyRun) return;
    if (!session || !reviewScope || service !== reviewScope.serviceId) throw new Error("任务或服务范围不可确认。");
    const [summary, snapshot] = await Promise.all([
      typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: service, run_id: run.id }),
      typedIPC.getRunSteps(run.id),
    ]);
    if (!current(ticket, origin) || summary.project_id !== project || summary.service_id !== service ||
        summary.detail?.id !== run.id || summary.detail.session_id !== session || snapshot.run.id !== run.id ||
        snapshot.run.session_id !== session || snapshot.run.metadata?.project_id !== project ||
        snapshot.steps.some((item) => item.run_id !== run.id || item.session_id !== session) ||
        !snapshot.steps.some((item) => item.status === "completed" && item.payload?.source === "run_attachment_v1" &&
          item.payload.project_id === project && item.payload.service_id === service &&
          item.payload.attachment_id === id && item.payload.sha256 === hash))
      throw new Error("任务或附件关联已变化，请重新读取。");
  };

  const select = async (step: AgentRunStep) => {
    if (!available || !step.payload) return;
    const id = String(step.payload.attachment_id), hash = String(step.payload.sha256), service = String(step.payload.service_id);
    const ticket = ++version.current, origin = ipc.agentBaseUrl;
    download.current?.abort(); setSelected(null); setPreview(""); setError(null); setLoading(true);
    try {
      await verifyAssociation(ticket, origin, id, hash, service);
      const result = await typedIPC.getAttachment(project, id);
      await verifyAssociation(ticket, origin, id, hash, service);
      if (!current(ticket, origin)) return;
      const file = result.attachment;
      if (file.id !== id || file.project_id !== project || file.service_id !== service || file.sha256 !== hash
          || !/^att_[a-f0-9]{32}$/.test(id) || !/^[a-f0-9]{64}$/.test(hash)
          || typeof file.original_name !== "string" || !file.original_name.trim() || [...file.original_name].length > 240
          || [...file.original_name].some((char) => char.charCodeAt(0) < 32 || (char.charCodeAt(0) >= 127 && char.charCodeAt(0) <= 159))
          || !Number.isInteger(file.size_bytes) || file.size_bytes < 0 || file.size_bytes > 10485760
          || file.quality_status !== "unverified" || !["parsed", "empty", "truncated", "parse_failed", "type_mismatch", "parser_unavailable", "ocr_not_attempted"].includes(file.parser_status)
          || typeof result.preview_text !== "string" || new TextEncoder().encode(result.preview_text).length > 65536
          // eslint-disable-next-line no-control-regex
          || result.preview_text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/).length > 2001)
        throw new Error("成果关联或预览不匹配，请刷新任务");
      setSelected(file); setPreview(result.preview_text);
    } catch (reason) { if (current(ticket, origin)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(ticket, origin)) setLoading(false); }
  };

  const save = async () => {
    if (!selected || !available) return;
    const ticket = version.current, origin = ipc.agentBaseUrl, file = selected;
    const controller = new AbortController(); download.current?.abort(); download.current = controller;
    setError(null); setLoading(true);
    try {
      await verifyAssociation(ticket, origin, file.id, file.sha256, file.service_id);
      const blob = await fetchAttachmentContent(file, origin, controller.signal);
      await verifyAssociation(ticket, origin, file.id, file.sha256, file.service_id);
      if (!current(ticket, origin)) return;
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = file.original_name.replace(/[\\/:*?"<>|]/g, "_");
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { if (current(ticket, origin)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(ticket, origin)) setLoading(false); }
  };

  if (!available || !rows.length) return null;
  return <div className="mt-2 space-y-2 text-xs"><p className="font-medium text-ink-1">关联成果 · {rows.length} 项{needle ? ` · 匹配 ${matchingRows.length} 项` : ""}</p><p className="text-ink-2">运行记录中的工具返回附件（可能复用旧成果；关联不证明由此任务生成，内容未验证）</p><div className="flex flex-wrap gap-1">
    {matchingRows.map((step) => <Button key={step.id} size="sm" variant="secondary" disabled={loading}
      onClick={() => void select(step)}>{String(step.payload?.filename || step.payload?.attachment_id || "查看成果")}</Button>)}</div>
    {needle && !matchingRows.length && <p>当前运行记录的附件名称或 ID 没有匹配项。</p>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {visible && <div className="space-y-1"><p>解析状态：{visible.parser_status}；质量未验证</p>
      <Button size="sm" disabled={loading} onClick={() => void save()}>下载原件</Button>
      {onPrepareAttachment && <Button size="sm" variant="secondary" disabled={loading || transferPending} onClick={() => onPrepareAttachment(visible, run.id)}>交接到附件与报告</Button>}
      {transferPending && <p>已有待处理附件，请先在附件与报告中打开或放弃。</p>}
      {project === activeProject && activeSession ? <PdfOriginalPreview attachment={visible} /> : visible.declared_type === "pdf" ? <p>切换到对应项目会话后可打开 PDF 原件预览；此处仍可下载原件。</p> : null}
      <XlsxTablePreview key={`${visible.id}:${visible.sha256}`} attachment={visible} preview={preview} />
      <SearchableTextPreview key={`${visible.id}:${visible.sha256}`} text={preview} label="任务成果预览" className="max-h-60 overflow-auto rounded bg-surface-2 p-2 whitespace-pre-wrap break-all" />
      {!preview && <p>（无可用预览）</p>}</div>}
  </div>;
}
