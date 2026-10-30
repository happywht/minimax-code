import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button, Textarea } from "../../ui";
import { reportDeliveryZip } from "./officeDeliveryZip";
import { DraftFileControl } from "./DraftFileControl";

type DeliveryNotes = { attachmentId: string; sha256: string; sources: string; method: string; limitations: string };

export function ReportDeliveryPackage({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element | null {
  const [sources, setSources] = useState(""), [method, setMethod] = useState(""), [limitations, setLimitations] = useState("");
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const invalidate = () => { epoch.current++; controller.current?.abort(); setBusy(false); setNotice(""); };
    invalidate(); setSources(""); setMethod(""); setLimitations(""); const off = ipc.onSideCar(invalidate);
    return () => { epoch.current++; controller.current?.abort(); off(); };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  const eligible = (attachment.declared_type === "docx" || attachment.declared_type === "pdf") && attachment.declared_type === attachment.recognized_type;
  if (!eligible) return null;
  const download = async () => {
    if (!project || !session || !service || busy || ipc.isMock) return;
    const ticket = ++epoch.current, abort = new AbortController(), notes = { sources, method, limitations };
    controller.current = abort;
    const current = () => {
      const active = useSessionStore.getState(), store = useAttachmentStore.getState();
      return ticket === epoch.current && !abort.signal.aborted && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && store.projectId === project && store.host === host && store.serviceId === service &&
        store.selected?.id === attachment.id && store.selected.sha256 === attachment.sha256;
    };
    const check = async () => {
      if (!current()) throw new Error("项目、会话、服务或所选附件已变化");
      const [identity, bound, result] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getAttachment(project, attachment.id)]);
      const row = result.attachment;
      if (!current() || identity.service_id !== service || bound.session.id !== session || bound.session.project_id !== project ||
          row.id !== attachment.id || row.project_id !== project || row.service_id !== service || row.sha256 !== attachment.sha256 ||
          JSON.stringify(row) !== JSON.stringify(attachment)) throw new Error("附件身份或归属已变化，请重新选择");
      return row;
    };
    setBusy(true); setNotice("");
    try {
      const row = await check();
      const blob = await fetchAttachmentContent(row, host, abort.signal), bytes = await blob.arrayBuffer();
      await check();
      if (!current()) return;
      const info = JSON.stringify({ version: 1, attachment: row, user_declaration: notes,
        notice: "用户填写的来源和处理说明未经自动核验，不代表系统从报告提取或确认的事实。附件身份和哈希不是数字签名；内容、来源及版式需人工核对。" }, null, 2);
      const zip = reportDeliveryZip(bytes, row.declared_type as "pdf" | "docx", info);
      if (!current()) return;
      const url = URL.createObjectURL(zip), anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${row.original_name.replace(/\.(docx|pdf)$/i, "").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_") || "报告"}-交付包.zip`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("已交给浏览器下载；请核对说明、来源和报告内容。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) { setBusy(false); controller.current = null; } }
  };
  return <details className="space-y-2 rounded border border-line p-2"><summary>准备报告交付包</summary>
    <p>下载 ZIP，包含此 DOCX/PDF 原件、附件身份和下方用户填写的说明。说明不会自动从报告或来源记录提取，未核验事实；不会修改或重新登记报告。请先填写实际来源、访问时间和处理范围。</p>
    <label className="block">资料来源与访问时间（用户填写）<Textarea aria-label="报告交付资料来源" rows={3} maxLength={2000} value={sources} disabled={busy} onChange={(event) => setSources(event.target.value)} /></label>
    <label className="block">成稿及处理口径（用户填写）<Textarea aria-label="报告交付处理口径" rows={3} maxLength={2000} value={method} disabled={busy} onChange={(event) => setMethod(event.target.value)} /></label>
    <label className="block">限制和待核对事项（用户填写）<Textarea aria-label="报告交付限制" rows={3} maxLength={2000} value={limitations} disabled={busy} onChange={(event) => setLimitations(event.target.value)} /></label>
    <DraftFileControl<DeliveryNotes> kind="report_delivery" disabled={busy}
      scope={project && session && service ? { host, service, project, session } : null}
      capture={() => ({ attachmentId: attachment.id, sha256: attachment.sha256, sources, method, limitations })}
      decode={async (value) => {
        if (!value || typeof value !== "object") throw new Error("交付说明格式无效");
        const data = value as Record<string, unknown>;
        if (data.attachmentId !== attachment.id || data.sha256 !== attachment.sha256) throw new Error("说明不属于当前报告附件及内容版本");
        if ([data.sources, data.method, data.limitations].some((item) => typeof item !== "string" || item.length > 2000)) throw new Error("交付说明字段无效或超过长度限制");
        return data as DeliveryNotes;
      }}
      apply={(data) => { setSources(data.sources); setMethod(data.method); setLimitations(data.limitations); }} />
    <p className="text-xs text-ink-2">说明随 ZIP 下载，也可单独导出为本地草稿并在同一服务、项目、会话和报告版本恢复；不会自动保存到项目。文件可能包含敏感信息。附件哈希和身份不是数字签名，不代表事实、来源、OCR 或版式已验证。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !session || !service || ipc.isMock} onClick={() => void download()}>下载报告及说明交付包</Button>
    {busy && <p role="status">正在核对附件并打包…</p>}{notice && <p role="status">{notice}</p>}
  </details>;
}
