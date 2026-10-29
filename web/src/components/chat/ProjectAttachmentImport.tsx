import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { uploadAttachment } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores/sessionStore";
import { Button } from "../../ui";

/** Save a file to the session's project without putting its contents in a message. */
export function ProjectAttachmentImport({ sessionId, projectId, host }: {
  sessionId: string | null; projectId: string | null | undefined; host: string;
}): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const operation = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setFile(null);
    if (input.current) input.current.value = "";
    if (operation.current) {
      operation.current.abort();
      setNotice("上下文已变化；导入结果可能已保存，请在原项目附件中核对后再重试。");
    } else setNotice("");
    return () => { operation.current?.abort(); };
  }, [sessionId, projectId, host]);

  const importFile = async () => {
    if (!file || !sessionId || !projectId || busy) return;
    const controller = new AbortController();
    operation.current = controller;
    let dispatched = false;
    const current = () => {
      const state = useSessionStore.getState();
      const session = state.sessions.find((item) => item.id === sessionId);
      return !controller.signal.aborted && ipc.agentBaseUrl === host &&
        state.currentSessionId === sessionId && state.currentProjectId === projectId && session?.project_id === projectId;
    };
    const unsubscribe = useSessionStore.subscribe(() => { if (!current()) controller.abort(); });
    const stopSidecar = ipc.onSideCar(() => controller.abort());
    setBusy(true); setNotice("");
    try {
      if (!current()) throw new Error("当前会话与所选项目不一致");
      const [identity, session] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(sessionId)]);
      if (!current() || session.session.id !== sessionId || session.session.project_id !== projectId || !identity.service_id)
        throw new Error("服务或会话项目已变化");
      dispatched = true;
      const row = await uploadAttachment(file, projectId, identity.service_id, host, controller.signal);
      if (!current() || row.project_id !== projectId || row.service_id !== identity.service_id ||
          !/^att_[a-f0-9]{32}$/.test(row.id) || !/^[a-f0-9]{64}$/.test(row.sha256) ||
          row.original_name !== file.name || row.size_bytes !== file.size || !Number.isInteger(row.version) || row.version < 1)
        throw new Error("返回的附件身份不一致");
      setFile(null);
      if (input.current) input.current.value = "";
      setNotice(`已保存 ${row.original_name} · v${row.version} 到当前项目。可在“项目附件与报告”中查看并选择加入草稿。`);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setNotice(dispatched
        ? `导入结果未确认（${reason}）；请先在原项目附件中核对，避免重复导入。`
        : `未开始导入：${reason}。`);
    } finally {
      unsubscribe(); stopSidecar();
      if (operation.current === controller) { operation.current = null; setBusy(false); }
    }
  };

  return <div className="flex flex-wrap items-center gap-2 px-2 py-1 text-xs text-ink-2">
    <label>导入项目附件 <input ref={input} aria-label="选择要导入项目的附件" type="file"
      disabled={busy || !sessionId || !projectId} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setNotice(""); }} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={!file || busy || !sessionId || !projectId}
      onClick={() => void importFile()}>{busy ? "导入中…" : "保存到项目"}</Button>
    <span>上限 10 MiB；保存不会发送到对话。</span>
    {notice && <p role="status" className="w-full break-words">{notice}</p>}
  </div>;
}
