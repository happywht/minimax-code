import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchArtifactContent } from "../../ipc/artifactContent";
import { useArtifactStore, useSessionStore } from "../../stores";
import type { TextArtifact } from "../../types/ipc";
import { Button } from "../../ui";

const MAX_BYTES = 2 * 1024 * 1024;

/** Edit a verified snapshot locally; the downloaded copy is never registered automatically. */
export function ArtifactEditCopy({ artifact }: { artifact: TextArtifact }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId);
  const session = useSessionStore((s) => s.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);
  const revision = useRef(0);

  useEffect(() => {
    const invalidate = () => { ++epoch.current; setText(null); setBusy(false); setNotice(""); };
    invalidate();
    const stopSession = useSessionStore.subscribe((s) => {
      if (s.currentProjectId !== project || s.currentSessionId !== session) invalidate();
    });
    const stopArtifacts = useArtifactStore.subscribe((s) => {
      if (s.projectId !== project || s.host !== host || s.serviceId !== artifact.service_id ||
          s.selected?.id !== artifact.id || s.selected.sha256 !== artifact.sha256) invalidate();
    });
    const stopConnection = ipc.onSideCar(invalidate);
    return () => { ++epoch.current; stopSession(); stopArtifacts(); stopConnection(); };
  }, [artifact.id, artifact.sha256, artifact.service_id, host, project, session]);

  const current = (ticket: number) => {
    const s = useSessionStore.getState(), a = useArtifactStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && s.currentProjectId === project &&
      s.currentSessionId === session && a.projectId === project && a.host === host &&
      a.serviceId === artifact.service_id && a.selected?.id === artifact.id && a.selected.sha256 === artifact.sha256;
  };
  const checkScope = async (ticket: number) => {
    if (!project || !session || !current(ticket)) throw new Error("请先选择当前项目和会话。");
    const [identity, result] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
    if (!current(ticket) || identity.service_id !== artifact.service_id || result.session.id !== session ||
        result.session.project_id !== project) throw new Error("项目、会话或服务已变化，请重新选择。");
  };
  const prepare = async () => {
    if (!project || !session || busy) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      await checkScope(ticket);
      const result = await typedIPC.getArtifact(project, artifact.id), row = result.artifact;
      if (!current(ticket) || row.id !== artifact.id || row.project_id !== project || row.service_id !== artifact.service_id ||
          row.snapshot_id !== artifact.snapshot_id || row.sha256 !== artifact.sha256 || row.size_bytes !== artifact.size_bytes ||
          row.version !== artifact.version || row.source_path !== artifact.source_path) throw new Error("成果身份已变化，请重新选择。");
      const blob = await fetchArtifactContent(row, host, new AbortController().signal);
      const bytes = await blob.arrayBuffer();
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
      if (bytes.byteLength !== row.size_bytes || hash !== row.sha256) throw new Error("成果字节校验失败。");
      const value = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
      await checkScope(ticket);
      if (current(ticket)) { revision.current++; setText(value); }
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const save = async () => {
    if (text === null || busy) return;
    const value = text, editRevision = revision.current, ticket = ++epoch.current;
    setBusy(true); setNotice("");
    try {
      const bytes = new TextEncoder().encode(value);
      if (bytes.length > MAX_BYTES || value.includes("\0")) throw new Error("副本不能包含 NUL，且不能超过 2 MiB。");
      await checkScope(ticket);
      if (!current(ticket) || revision.current !== editRevision) throw new Error("草稿已变化，请重新下载。");
      const filename = artifact.source_path.split("/").pop() || "artifact.txt";
      const dot = filename.lastIndexOf(".");
      const copyName = dot > 0 ? `${filename.slice(0, dot)}-edited${filename.slice(dot)}` : `${filename}-edited.txt`;
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = copyName; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("已发起副本下载；副本未登记，内容和 CSV 格式需自行核对。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const append = async () => {
    if (text === null || !project || !session || busy) return;
    const value = text, editRevision = revision.current, ticket = ++epoch.current;
    setBusy(true); setNotice("");
    try {
      if (value.includes("\0")) throw new Error("副本不能包含 NUL。");
      await checkScope(ticket);
      const { artifact: row } = await typedIPC.getArtifact(project, artifact.id);
      if (!current(ticket) || row.id !== artifact.id || row.project_id !== project ||
          row.service_id !== artifact.service_id || row.snapshot_id !== artifact.snapshot_id ||
          row.sha256 !== artifact.sha256 || row.size_bytes !== artifact.size_bytes ||
          row.version !== artifact.version || row.source_path !== artifact.source_path)
        throw new Error("成果身份已变化，请重新选择。");
      await checkScope(ticket);
      if (!current(ticket) || revision.current !== editRevision) throw new Error("副本已变化，请重新操作。");
      const content = ["以下是用户编辑的文本成果副本，尚未登记，内容和业务结果需核对：",
        `来源路径：${row.source_path} · 版本：${row.version}`,
        `来源成果 ID：${row.id} · 原快照 SHA-256：${row.sha256}`,
        "--- 编辑副本正文开始 ---", value, "--- 编辑副本正文结束 ---"].join("\n");
      const detail: { host: string; project: string; session: string; text: string; result?: string } =
        { host, project, session, text: content };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "当前聊天输入区未打开，请先打开聊天再操作。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2 text-sm">
    <p>编辑已登记快照的本地副本，可下载或追加到聊天草稿；不会修改项目源文件或已登记版本。切换成果、项目、会话或服务会丢弃编辑。</p>
    {text === null ? <Button type="button" size="sm" variant="secondary" disabled={busy || !project || !session || ipc.isMock} onClick={() => void prepare()}>编辑文本副本</Button> : <>
      <textarea aria-label="文本成果副本" className="min-h-48 w-full rounded border border-line bg-surface-1 p-2 font-mono" value={text} disabled={busy}
        onChange={(event) => { revision.current++; setText(event.target.value); }} />
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy} onClick={() => void save()}>下载编辑副本</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void append()}>追加编辑副本到聊天草稿</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { revision.current++; setText(null); setNotice(""); }}>丢弃副本</Button></div>
    </>}
    {notice && <p role="status">{notice}</p>}
    {notice === "已添加到当前会话草稿，尚未发送。" && <Button type="button" size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
