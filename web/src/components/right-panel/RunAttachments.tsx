import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import type { AgentRun, AgentRunStep, AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";
import { PdfOriginalPreview } from "../panels/PdfOriginalPreview";
import { XlsxTablePreview } from "../panels/XlsxTablePreview";
import { SearchableTextPreview } from "../panels/SearchableTextPreview";

export function RunAttachments({ run, steps, reviewScope }: { run: AgentRun; steps: AgentRunStep[]; reviewScope?: { sessionId: string; projectId: string; serviceId: string } }): JSX.Element | null {
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

  const select = async (step: AgentRunStep) => {
    if (!available || !step.payload) return;
    const id = String(step.payload.attachment_id), hash = String(step.payload.sha256), service = String(step.payload.service_id);
    const ticket = ++version.current, origin = ipc.agentBaseUrl;
    download.current?.abort(); setSelected(null); setPreview(""); setError(null); setLoading(true);
    try {
      const result = await typedIPC.getAttachment(project, id);
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
      const blob = await fetchAttachmentContent(file, origin, controller.signal);
      if (!current(ticket, origin)) return;
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = file.original_name.replace(/[\\/:*?"<>|]/g, "_");
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { if (current(ticket, origin)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(ticket, origin)) setLoading(false); }
  };

  if (!available || !rows.length) return null;
  return <div className="mt-2 space-y-2 text-xs"><p className="font-medium text-ink-1">关联成果 · {rows.length} 项</p><p className="text-ink-2">任务生成工具返回的关联成果（可能为幂等复用；内容未验证）</p><div className="flex flex-wrap gap-1">
    {rows.map((step) => <Button key={step.id} size="sm" variant="secondary" disabled={loading}
      onClick={() => void select(step)}>{String(step.payload?.filename || "查看成果")}</Button>)}</div>
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {visible && <div className="space-y-1"><p>解析状态：{visible.parser_status}；质量未验证</p>
      <Button size="sm" disabled={loading} onClick={() => void save()}>下载原件</Button>
      {project === activeProject && activeSession ? <PdfOriginalPreview attachment={visible} /> : visible.declared_type === "pdf" ? <p>切换到对应项目会话后可打开 PDF 原件预览；此处仍可下载原件。</p> : null}
      <XlsxTablePreview key={`${visible.id}:${visible.sha256}`} attachment={visible} preview={preview} />
      <SearchableTextPreview key={`${visible.id}:${visible.sha256}`} text={preview} label="任务成果预览" className="max-h-60 overflow-auto rounded bg-surface-2 p-2 whitespace-pre-wrap break-all" />
      {!preview && <p>（无可用预览）</p>}</div>}
  </div>;
}
