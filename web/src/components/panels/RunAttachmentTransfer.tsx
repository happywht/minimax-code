import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button } from "../../ui";
import type { RunAttachmentTransfer as Transfer } from "./DeliveryWorkspace";

export function RunAttachmentTransfer({ transfer, project, session, service, host, onClear }: {
  transfer: Transfer; project: string | null; session: string | null; service: string | null; host: string; onClear: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0);
  useEffect(() => { epoch.current++; setBusy(false); setNotice(""); return () => { epoch.current++; }; }, [transfer, project, session, service, host]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setBusy(false); setNotice("连接变化，请重新确认服务后重试。"); }), []);
  const open = async () => {
    const { attachment } = transfer;
    if (!project || !session || !service || busy) return;
    const ticket = ++epoch.current;
    const current = () => {
      const active = useSessionStore.getState(), store = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && store.projectId === project && store.host === host && store.serviceId === service;
    };
    if (!current() || attachment.project_id !== project || attachment.service_id !== service) {
      setNotice("项目或服务身份不匹配，请重新选择。"); return;
    }
    setBusy(true); setNotice("");
    try {
      await useAttachmentStore.getState().select(attachment.id, attachment.sha256);
      if (!current()) return;
      const state = useAttachmentStore.getState();
      if (state.selected?.id !== attachment.id || state.selected.sha256 !== attachment.sha256)
        throw new Error(state.error || "附件身份已变化，请重新选择");
      onClear();
    } catch (error) { if (current()) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-3" aria-label="待处理任务附件">
    <p>待打开任务关联附件：{transfer.attachment.original_name} · v{transfer.attachment.version} · 任务 {transfer.runId}</p>
    <p className="break-all text-xs">{transfer.attachment.id} · SHA-256 {transfer.attachment.sha256}。关联不证明由该任务生成，内容未经核验。</p>
    <p>打开后可按文件支持范围预览、读取或用于报告；不会自动改写草稿或原件。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !session || !service} onClick={() => void open()}>核对并打开任务附件</Button>
    <Button type="button" variant="ghost" disabled={busy} onClick={onClear}>放弃交接</Button>
    {busy && <p role="status">正在重新核对附件…</p>}{notice && <p role="alert">{notice}</p>}
  </div>;
}
