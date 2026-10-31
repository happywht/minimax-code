import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { uploadAttachment } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores/sessionStore";
import type { RemoteHost } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

type Preview = { path: string; sha256: string; serviceId: string; blob: Blob };
type Pending = { service: string; project: string; session: string; host: string; path: string; name: string; sha256: string; size: number };
const hostIdentity = (host: RemoteHost) => JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.remote_port, host.enabled]);
const validName = (name: string) => !!name.trim() && [...name].length <= 240 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(name);

/** Explicitly import the already verified preview snapshot into the selected session's project. */
export function HostPreviewAttachment({ host, preview, active }: { host: RemoteHost; preview: Preview | null; active: boolean }): JSX.Element {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const projectId = useSessionStore((state) => state.currentProjectId);
  const base = ipc.agentBaseUrl;
  const key = `minimax:host-preview-attachment:${JSON.stringify([base, host.id, projectId])}`;
  const scope = JSON.stringify([base, hostIdentity(host), sessionId, projectId, active, preview?.path, preview?.sha256, preview?.serviceId]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const operation = useRef<AbortController | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const readPending = (): Pending | null | "unavailable" => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return null;
      const record: unknown = JSON.parse(raw);
      if (!record || typeof record !== "object" || Array.isArray(record)) return "unavailable";
      const value = record as Record<string, unknown>;
      if (!["service", "project", "session", "host", "path", "name", "sha256"].every((field) => typeof value[field] === "string" && (value[field] as string).length > 0 && (value[field] as string).length <= 4096) || !Number.isSafeInteger(value.size) || (value.size as number) < 0 || (value.size as number) > 10485760) return "unavailable";
      return record as Pending;
    } catch { return "unavailable"; }
  };
  const [pending, setPending] = useState<Pending | null | "unavailable">(() => readPending());
  useEffect(() => {
    operation.current?.abort();
    setPending(readPending()); setNotice("");
    return () => { operation.current?.abort(); };
  }, [scope]);
  useEffect(() => { setName(preview?.path.split("/").pop() || ""); }, [preview?.path, preview?.sha256]);
  useEffect(() => ipc.onSideCar(() => { operation.current?.abort(); setNotice("连接已变化；如已提交，请先在原项目核对附件。"); }), []);

  const save = async () => {
    if (operation.current || pending || !preview || !active || !host.enabled || !sessionId || !projectId || !validName(name)) return;
    const abort = new AbortController(), snapshot = scope, selected = preview, filename = name;
    operation.current = abort; setBusy(true); setNotice("");
    let dispatched = false;
    const current = () => {
      const state = useSessionStore.getState();
      return !abort.signal.aborted && scopeRef.current === snapshot && ipc.agentBaseUrl === base && state.currentSessionId === sessionId && state.currentProjectId === projectId && state.sessions.some((item) => item.id === sessionId && item.project_id === projectId);
    };
    const stopSession = useSessionStore.subscribe(() => { if (!current()) abort.abort(); });
    const timer = window.setTimeout(() => abort.abort(), 60000);
    try {
      const verify = async () => {
        const [identity, session, storedHost] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(sessionId), typedIPC.getHost(host.id)]);
        if (!current() || identity.service_id !== selected.serviceId || session.session.id !== sessionId || session.session.project_id !== projectId || hostIdentity(storedHost.host) !== hostIdentity(host)) throw new Error("服务、主机或会话项目已变化");
      };
      await verify();
      const accepted = await requestConfirmation({ title: "将预览快照保存为项目附件？", description: `将 ${host.username}@${host.hostname}:${host.port} 的 ${selected.path} 的完整预览快照保存到项目 ${projectId}，附件名为 ${filename}，SHA-256 为 ${selected.sha256}。文件可能包含凭据；此操作不会发送到对话。`, confirmLabel: "确认保存附件" });
      if (!accepted || !current()) return;
      await verify();
      if (readPending()) throw new Error("已有待核对记录或浏览器存储不可用");
      const record: Pending = { service: selected.serviceId, project: projectId, session: sessionId, host: host.id, path: selected.path, name: filename, sha256: selected.sha256, size: selected.blob.size };
      sessionStorage.setItem(key, JSON.stringify(record)); setPending(record);
      dispatched = true;
      const row = await uploadAttachment(new File([selected.blob], filename), projectId, selected.serviceId, base, abort.signal);
      await verify();
      if (row.project_id !== projectId || row.service_id !== selected.serviceId || !/^att_[a-f0-9]{32}$/.test(row.id) || row.sha256 !== selected.sha256 || row.original_name !== filename || row.size_bytes !== selected.blob.size || !Number.isInteger(row.version) || row.version < 1 || JSON.stringify(readPending()) !== JSON.stringify(record)) throw new Error("附件回执或待核对记录不一致");
      sessionStorage.removeItem(key); setPending(null);
      setNotice(`已保存附件 ${row.original_name} · v${row.version}（${row.id}），SHA-256 与预览一致。可在项目附件中查看并手动加入草稿。`);
    } catch {
      if (scopeRef.current === snapshot) {
        setPending(readPending());
        setNotice(dispatched ? "保存结果未确认；请先在原服务、原项目附件中按文件名和 SHA-256 核对，避免重复导入。" : "未提交附件；请核对当前会话、项目、主机、服务和浏览器存储后重试。");
      }
    } finally {
      window.clearTimeout(timer); stopSession();
      if (operation.current === abort) { operation.current = null; setBusy(false); }
    }
  };
  const clear = async () => {
    if (!pending || operation.current) return;
    const expected = pending, snapshot = scope;
    const accepted = await requestConfirmation({ title: "清除待核对的附件记录？", description: "请先在原服务和项目的附件列表核对。清除仅删除本标签页的记录，不会删除或撤销可能已保存的附件。", confirmLabel: "已核对，清除记录" });
    if (!accepted || snapshot !== scopeRef.current || JSON.stringify(readPending()) !== JSON.stringify(expected)) return;
    try { sessionStorage.removeItem(key); setPending(null); setNotice("待核对记录已清除，附件未被修改。"); } catch { setNotice("无法清除记录，请检查浏览器存储。"); }
  };
  return <div className="space-y-1 border-t border-line pt-2 text-xs">
    <p>保存预览快照为当前会话的项目附件</p>
    <p>仅手动保存完整预览文件（最多 10 MiB）；屏幕可能只显示前 64 KiB，过滤的控制字符仍在附件中。</p><p>内容可能包含凭据，请按需操作。不会自动发送到对话。</p>
    <p className="break-all">当前项目：{projectId || "未选择"} · 会话：{sessionId || "未选择"}</p>
    {preview && <label className="block">附件显示名<Input aria-label="预览快照附件名称" value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>}
    <Button type="button" size="sm" variant="secondary" disabled={busy || !!pending || !preview || !active || !host.enabled || !sessionId || !projectId || !validName(name)} onClick={() => void save()}>{busy ? "保存中…" : "保存预览到项目附件"}</Button>
    {pending && <div role="alert" className="break-all"><p>上次保存结果待核对：{pending === "unavailable" ? "记录不可读取或浏览器存储不可用" : `服务 ${pending.service} · 项目 ${pending.project} · 会话 ${pending.session} · 主机 ${pending.host} · 路径 ${pending.path} · 名称 ${pending.name} · SHA-256 ${pending.sha256} · ${pending.size} 字节`}</p><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void clear()}>核对后清除记录</Button></div>}
    {notice && <p role="status" className="break-all">{notice}</p>}
  </div>;
}
