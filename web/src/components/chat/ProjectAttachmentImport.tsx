import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ipc, typedIPC } from "../../ipc";
import { uploadAttachment } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores/sessionStore";
import { Button, Modal } from "../../ui";
import type { AttachmentOriginal } from "../../types/ipc";

/** Save a file to the session's project without putting its contents in a message. */
export function ProjectAttachmentImport({ sessionId, projectId, host, open, onClose }: {
  sessionId: string | null; projectId: string | null | undefined; host: string; open: boolean; onClose: () => void;
}): JSX.Element | null {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState<AttachmentOriginal | null>(null);
  const revision = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // This ref is an operation generation, not a DOM node; invalidate it on scope changes and unmount.
    const generation = revision;
    generation.current++; setSaved(null); setFile(null);
    if (input.current) input.current.value = "";
    if (operation.current) {
      operation.current.abort();
      setNotice("上下文已变化；导入结果可能已保存，请在原项目附件中核对后再重试。");
    } else setNotice("");
    return () => { generation.current++; operation.current?.abort(); };
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
      setSaved(row); setFile(null);
      if (input.current) input.current.value = "";
      setNotice(`已保存 ${row.original_name} · v${row.version} 到当前项目。尚未发送到对话，也不代表模型已读取原件。`);
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

  const addReference = async () => {
    if (!saved || !sessionId || !projectId || busy) return;
    const selected = saved, ticket = ++revision.current;
    const current = () => {
      const state = useSessionStore.getState();
      return ticket === revision.current && ipc.agentBaseUrl === host && state.currentSessionId === sessionId &&
        state.currentProjectId === projectId && state.sessions.some((item) => item.id === sessionId && item.project_id === projectId && !item.archived);
    };
    const off = ipc.onSideCar(() => { revision.current++; });
    setBusy(true); setNotice("");
    try {
      const [identity, session, result] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getSession(sessionId), typedIPC.getAttachment(projectId, selected.id),
      ]);
      const after = await typedIPC.getServiceIdentity();
      const row = result.attachment;
      if (!current() || identity.service_id !== selected.service_id || after.service_id !== selected.service_id ||
          session.session.id !== sessionId || session.session.project_id !== projectId || session.session.archived || row.id !== selected.id ||
          row.project_id !== projectId || row.service_id !== selected.service_id || row.sha256 !== selected.sha256 ||
          row.snapshot_id !== selected.snapshot_id || row.size_bytes !== selected.size_bytes || row.version !== selected.version ||
          row.original_name !== selected.original_name || row.declared_type !== selected.declared_type ||
          row.recognized_type !== selected.recognized_type || row.parser_status !== selected.parser_status ||
          row.parser_scope !== selected.parser_scope || row.parser_reason !== selected.parser_reason) throw new Error("服务、会话或附件已变化，请重新选择");
      const text = ["项目附件引用（仅元数据加入草稿，尚未发送；未读取原件）：",
        `文件名：${row.original_name} · 版本：${row.version}`, `附件 ID：${row.id}`, `SHA-256：${row.sha256}`,
        `声明类型：${row.declared_type} · 识别类型：${row.recognized_type}`,
        `解析状态：${row.parser_status} · 范围：${row.parser_scope} · 原因：${row.parser_reason}`,
        "如当前运行环境支持项目附件读取工具，可按附件 ID 进一步读取其支持的内容；请区分原件、解析预览和未验证事实。"].join("\n");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      if (current()) setNotice(detail.result ?? "当前聊天输入区未就绪，请在项目附件中重新选择。");
    } catch (error) { if (current()) setNotice(`未加入引用：${error instanceof Error ? error.message : String(error)}`); }
    finally { off(); setBusy(false); }
  };

  if (!open) return null;
  return createPortal(<Modal title="添加文件到项目" onClose={onClose}>
    <form className="space-y-3 text-sm" onSubmit={(event) => { event.preventDefault(); event.stopPropagation(); }}>
      <p className="text-xs text-ink-2">支持 PDF、Word、Excel 等文件，最大 10 MiB。文件先保存到当前项目；可将文件引用加入聊天草稿。保存或引用不代表模型已读取原件。</p>
      {(!sessionId || !projectId) && <p role="status" className="text-status-warning">请先选择项目和会话。</p>}
      <label className="block text-xs text-ink-1">选择文件 <input ref={input} aria-label="选择要导入项目的附件" type="file"
        disabled={busy || !sessionId || !projectId} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setSaved(null); setNotice(""); }} /></label>
      {file && <p className="break-all text-xs text-ink-1">已选择：{file.name}（{file.size} 字节）</p>}
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="secondary" disabled={!file || busy || !sessionId || !projectId}
        onClick={() => void importFile()}>{busy ? "处理中…" : "保存到项目"}</Button>
        {saved && <Button type="button" size="sm" disabled={busy} onClick={() => void addReference()}>加入引用到草稿</Button>}</div>
      {notice && <p role="status" className="break-words text-xs text-ink-1">{notice}</p>}
      {file && <p className="text-xs text-ink-2">关闭窗口会保留当前选择；切换项目或会话将清除未上传文件。</p>}
    </form>
  </Modal>, document.body);
}
