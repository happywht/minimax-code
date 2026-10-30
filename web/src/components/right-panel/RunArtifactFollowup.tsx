import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { TextArtifact } from "../../types/ipc";
import { Button } from "../../ui";

/** Prepare a reference to the selected run-linked snapshot, never send it. */
export function RunArtifactFollowup({ artifact, session }: { artifact: TextArtifact; session: string }): JSX.Element {
  const host = ipc.agentBaseUrl, project = artifact.project_id;
  const [excerpt, setExcerpt] = useState(false), [prepared, setPrepared] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    const off = ipc.onSideCar(() => { epoch.current++; setPrepared(null); setBusy(false); setMessage("连接变化，请重新准备。"); });
    const stop = useSessionStore.subscribe((state) => {
      if (state.currentSessionId !== session || state.currentProjectId !== project) { epoch.current++; setPrepared(null); setBusy(false); }
    });
    return () => { epoch.current++; off(); stop(); };
  }, [host, project, session, artifact.id]);
  const read = async (ticket: number) => {
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    const [identity, sessionResult, result] = await Promise.all([
      typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getArtifact(project, artifact.id),
    ]);
    const [after, sessionAfter] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
    const row = result.artifact;
    if (!current() || identity.service_id !== artifact.service_id || after.service_id !== artifact.service_id ||
        sessionResult.session.id !== session || sessionResult.session.project_id !== project ||
        sessionAfter.session.id !== session || sessionAfter.session.project_id !== project ||
        row.id !== artifact.id || row.project_id !== project || row.service_id !== artifact.service_id ||
        row.run_id !== artifact.run_id || row.sha256 !== artifact.sha256 || row.snapshot_id !== artifact.snapshot_id ||
        row.version !== artifact.version || row.source_path !== artifact.source_path || row.size_bytes !== artifact.size_bytes ||
        row.mime_type !== artifact.mime_type || row.verification_status !== artifact.verification_status || typeof result.preview_text !== "string")
      throw new Error("成果、会话或服务归属已变化，请重新选择。");
    const lines = ["任务关联文本成果引用（登记关联不证明来源，内容和业务结果未经验证）：",
      `路径：${row.source_path} · 版本：${row.version}`, `成果 ID：${row.id} · SHA-256：${row.sha256}`,
      `关联运行：${row.run_id} · 大小：${row.size_bytes} 字节 · 类型：${row.mime_type}`];
    lines.push(excerpt ? `预览摘录（最多 1200 字，可能不完整）：\n${Array.from(result.preview_text).slice(0, 1200).join("") || "（预览为空）"}` : "仅附身份信息，未加入正文。");
    return lines.join("\n");
  };
  const act = async (append: boolean) => {
    const expected = prepared, ticket = ++epoch.current; setBusy(true); setMessage("");
    try {
      const text = await read(ticket);
      if (ticket !== epoch.current) return;
      if (!append) { setPrepared(text); return; }
      if (!expected || text !== expected) { setPrepared(null); throw new Error("成果内容或身份已变化，请重新准备。"); }
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "聊天输入区未就绪，请稍后重试。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") setPrepared(null);
    } catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="mt-2 space-y-2">
    <label className="flex items-center gap-2"><input type="checkbox" checked={excerpt} disabled={busy} onChange={(event) => { setExcerpt(event.target.checked); setPrepared(null); }} />加入最多 1200 字预览摘录</label>
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void act(false)}>准备成果引用草稿</Button>
    {prepared && <div className="space-y-2 rounded border border-line p-2"><p>请核对引用；追加不会发送。</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{prepared}</pre>
      <Button type="button" size="sm" disabled={busy} onClick={() => void act(true)}>追加到当前会话草稿</Button>{" "}
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPrepared(null)}>取消</Button></div>}
    {message && <p role="status">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button type="button" size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
