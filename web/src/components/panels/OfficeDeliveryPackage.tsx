import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeGetResult } from "../../types/ipc";
import { Button } from "../../ui";
import { officeDeliveryZip } from "./officeDeliveryZip";

export function OfficeDeliveryPackage({ selected, project, session, service, host }: {
  selected: OfficeGetResult; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), controller = useRef<AbortController | null>(null);
  const { delivery, attachment } = selected;
  useEffect(() => {
    const invalidate = () => { epoch.current++; controller.current?.abort(); setBusy(false); setNotice(""); };
    invalidate(); const off = ipc.onSideCar(invalidate);
    return () => { epoch.current++; controller.current?.abort(); off(); };
  }, [project, session, service, host, delivery.id, attachment.id, attachment.sha256]);
  const download = async () => {
    if (!project || !session || !service || busy || ipc.isMock) return;
    const ticket = ++epoch.current, abort = new AbortController(); controller.current = abort;
    const current = () => {
      const active = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
      return ticket === epoch.current && !abort.signal.aborted && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && office.projectId === project && office.host === host && office.serviceId === service &&
        office.selected?.delivery.id === delivery.id && office.selected.attachment.id === attachment.id && office.selected.attachment.sha256 === attachment.sha256;
    };
    const check = async () => {
      if (!current()) throw new Error("项目、会话、服务或所选成果已变化");
      const [identity, bound, fresh] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getOfficeDelivery(project, delivery.id)]);
      if (!current() || identity.service_id !== service || bound.session.id !== session || bound.session.project_id !== project ||
          fresh.delivery.project_id !== project || fresh.delivery.service_id !== service || fresh.delivery.id !== delivery.id ||
          fresh.delivery.attachment_id !== attachment.id || fresh.attachment.project_id !== project || fresh.attachment.service_id !== service ||
          fresh.attachment.id !== attachment.id || fresh.attachment.sha256 !== attachment.sha256 ||
          fresh.delivery.validation_report.file_sha256 !== attachment.sha256 || fresh.delivery.validation_report.size_bytes !== attachment.size_bytes ||
          fresh.delivery.input_sha256 !== fresh.delivery.validation_report.input_sha256 ||
          JSON.stringify([fresh.delivery, fresh.attachment]) !== JSON.stringify([delivery, attachment]))
        throw new Error("成果、回执或归属已变化，请重新选择");
      return fresh;
    };
    setBusy(true); setNotice("");
    try {
      const fresh = await check();
      const blob = await fetchAttachmentContent(fresh.attachment, host, abort.signal);
      const bytes = await blob.arrayBuffer();
      await check();
      if (!current()) return;
      const receipt = JSON.stringify({ version: 1, delivery: {
        id: fresh.delivery.id, project_id: fresh.delivery.project_id, service_id: fresh.delivery.service_id,
        attachment_id: fresh.delivery.attachment_id, run_id: fresh.delivery.run_id, input_sha256: fresh.delivery.input_sha256,
        created_at: fresh.delivery.created_at, validation_report: fresh.delivery.validation_report,
      }, attachment: fresh.attachment }, null, 2);
      const zip = officeDeliveryZip(bytes, receipt);
      if (!current()) return;
      const url = URL.createObjectURL(zip), anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${attachment.original_name.replace(/\.xlsx$/i, "").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_") || "office"}-交付包.zip`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("已交给浏览器下载；请与接收者确认处理说明及业务数据。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) { setBusy(false); controller.current = null; } }
  };
  return <div className="space-y-1"><Button type="button" variant="secondary" disabled={busy || !project || !session || !service || ipc.isMock} onClick={() => void download()}>下载 XLSX 与回执交付包</Button>
    <p className="text-xs text-ink-2">ZIP 包含原始 XLSX、成果身份及校验回执和说明；回执非数字签名，处理说明可能含敏感信息。业务和版式未核验。</p>
    {busy && <p role="status">正在重新核对成果并打包…</p>}{notice && <p role="status">{notice}</p>}</div>;
}
