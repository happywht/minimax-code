import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useArtifactStore, useSessionStore } from "../../stores";
import type { TextArtifact } from "../../types/ipc";
import { Button } from "../../ui";

/** Prepare a reviewable reference to a registered snapshot, without sending it. */
export function ArtifactFollowupDraft({ artifact }: { artifact: TextArtifact }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId);
  const session = useSessionStore((s) => s.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [excerpt, setExcerpt] = useState(false);
  const [prepared, setPrepared] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    ++epoch.current; setPrepared(null); setMessage(""); setBusy(false); setExcerpt(false);
    return () => { ++epoch.current; };
  }, [artifact.id, artifact.sha256, project, session, host]);
  useEffect(() => ipc.onSideCar(() => {
    ++epoch.current; setPrepared(null); setBusy(false); setMessage("连接变化，请重新准备草稿。");
  }), []);

  const read = async (ticket: number): Promise<string> => {
    if (!project || !session) throw new Error("请先选择项目和会话。");
    let changed = false;
    const stopSession = useSessionStore.subscribe((state) => {
      if (state.currentProjectId !== project || state.currentSessionId !== session) changed = true;
    });
    const stopArtifacts = useArtifactStore.subscribe((state) => {
      if (state.projectId !== project || state.host !== host || state.serviceId !== artifact.service_id ||
          state.selected?.id !== artifact.id || state.selected.sha256 !== artifact.sha256) changed = true;
    });
    const stopConnection = ipc.onSideCar(() => { changed = true; });
    const current = () => {
      const scope = useSessionStore.getState(), store = useArtifactStore.getState();
      return !changed && ticket === epoch.current && ipc.agentBaseUrl === host &&
        scope.currentProjectId === project && scope.currentSessionId === session &&
        store.projectId === project && store.host === host && store.serviceId === artifact.service_id &&
        store.selected?.id === artifact.id && store.selected.sha256 === artifact.sha256;
    };
    try {
      const [identity, sessionResult, result] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getArtifact(project, artifact.id),
      ]);
      const [afterIdentity, afterSession] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
      const row = result.artifact;
      if (!current() || identity.service_id !== artifact.service_id || afterIdentity.service_id !== artifact.service_id ||
          sessionResult.session.id !== session || sessionResult.session.project_id !== project ||
          afterSession.session.id !== session || afterSession.session.project_id !== project ||
          row.id !== artifact.id || row.project_id !== project || row.service_id !== artifact.service_id ||
          row.sha256 !== artifact.sha256 || row.snapshot_id !== artifact.snapshot_id || row.version !== artifact.version ||
          row.source_path !== artifact.source_path || row.run_id !== artifact.run_id || row.size_bytes !== artifact.size_bytes ||
          row.mime_type !== artifact.mime_type || row.verification_status !== artifact.verification_status ||
          typeof result.preview_text !== "string") throw new Error("成果、会话或服务归属已变化，请重新选择。");
      const lines = ["项目文本成果引用（已登记快照，内容和业务结果未经验证；请核对原始成果）：",
        `路径：${row.source_path} · 版本：${row.version}`, `成果 ID：${row.id} · SHA-256：${row.sha256}`,
        `大小：${row.size_bytes} 字节 · 类型：${row.mime_type}`,
        row.run_id ? `登记关联运行：${row.run_id}（关联不证明由该运行生成）` : "未登记关联运行"];
      if (excerpt) {
        const chars = Array.from(result.preview_text);
        lines.push("以下为最多 1200 字的有界预览摘录，不保证覆盖全文，也不是已验证事实：",
          chars.slice(0, 1200).join("") || "（预览为空）");
      } else lines.push("仅附身份信息，未将成果正文加入草稿。");
      if (row.mime_type === "text/plain" || row.mime_type === "text/markdown")
        lines.push("如需生成 DOCX 或 PDF，请说明标题、文件名和所需格式；可使用此成果快照生成，Markdown 按纯文本处理。");
      return lines.join("\n");
    } finally { stopSession(); stopArtifacts(); stopConnection(); }
  };
  const prepare = async () => {
    const ticket = ++epoch.current; setBusy(true); setPrepared(null); setMessage("");
    try { const text = await read(ticket); if (ticket === epoch.current) setPrepared(text); }
    catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const append = async () => {
    if (!prepared || !project || !session || busy) return;
    const expected = prepared, ticket = ++epoch.current; setBusy(true); setMessage("");
    try {
      const text = await read(ticket);
      if (ticket !== epoch.current) return;
      if (text !== expected) { setPrepared(null); throw new Error("成果内容或身份已变化，请重新准备。"); }
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先打开聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") setPrepared(null);
    } catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 text-xs">
    <label className="flex items-center gap-2"><input type="checkbox" checked={excerpt} disabled={busy}
      onChange={(event) => { setExcerpt(event.target.checked); setPrepared(null); }} />同时加入最多 1200 字预览摘录</label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !project || !session} onClick={() => void prepare()}>准备成果引用草稿</Button>
    {prepared && <div className="space-y-2 rounded border border-line p-2"><p>请核对以下内容；追加不会发送或执行。</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-1 p-2">{prepared}</pre>
      <div className="flex gap-2"><Button type="button" size="sm" disabled={busy} onClick={() => void append()}>追加到当前会话草稿</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPrepared(null)}>取消</Button></div></div>}
    {message && <p role="status">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button type="button" size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
