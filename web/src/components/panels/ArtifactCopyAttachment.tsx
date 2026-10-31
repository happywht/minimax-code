import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { uploadAttachment } from "../../ipc/attachmentContent";
import { useArtifactStore, useSessionStore } from "../../stores";
import type { TextArtifact } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

type Pending = { service: string; project: string; session: string; artifact: string; sourceHash: string; name: string; sha256: string; size: number };
const validName = (name: string) => !!name.trim() && [...name].length <= 240 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(name);

/** Save an edited snapshot as a new project attachment, leaving its source untouched. */
export function ArtifactCopyAttachment({ artifact, text }: { artifact: TextArtifact; text: string }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId);
  const session = useSessionStore((s) => s.currentSessionId);
  const base = ipc.agentBaseUrl;
  const key = `minimax:artifact-copy-attachment:${JSON.stringify([base, project])}`;
  const scope = JSON.stringify([base, project, session, artifact.id, artifact.sha256, artifact.service_id, text]);
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const operation = useRef<AbortController | null>(null);
  const initialName = artifact.source_path.split("/").pop() || "artifact.txt";
  const dot = initialName.lastIndexOf(".");
  const [name, setName] = useState(dot > 0 ? `${initialName.slice(0, dot)}-edited${initialName.slice(dot)}` : `${initialName}-edited.txt`);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const readPending = (): Pending | null | "unavailable" => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return null;
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) return "unavailable";
      const row = value as Record<string, unknown>;
      if (!["service", "project", "session", "artifact", "sourceHash", "name", "sha256"].every((field) => typeof row[field] === "string" && (row[field] as string).length > 0 && (row[field] as string).length <= 4096) || !Number.isSafeInteger(row.size) || (row.size as number) < 0 || (row.size as number) > 2 * 1024 * 1024) return "unavailable";
      return value as Pending;
    } catch { return "unavailable"; }
  };
  const [pending, setPending] = useState<Pending | null | "unavailable">(() => readPending());
  useEffect(() => {
    operation.current?.abort(); setPending(readPending()); setNotice("");
    return () => { operation.current?.abort(); };
  }, [base, project, session, artifact.id, artifact.sha256, artifact.service_id]);
  useEffect(() => ipc.onSideCar(() => { operation.current?.abort(); setPending(readPending()); }), [base, project]);

  const save = async () => {
    if (operation.current || pending || !project || !session || !validName(name)) return;
    const abort = new AbortController(), snapshot = scope, filename = name;
    operation.current = abort; setBusy(true); setNotice("");
    let dispatched = false;
    const current = () => {
      const s = useSessionStore.getState(), a = useArtifactStore.getState();
      return !abort.signal.aborted && scopeRef.current === snapshot && ipc.agentBaseUrl === base &&
        s.currentProjectId === project && s.currentSessionId === session && a.projectId === project &&
        a.host === base && a.serviceId === artifact.service_id && a.selected?.id === artifact.id && a.selected.sha256 === artifact.sha256;
    };
    const stop = useSessionStore.subscribe(() => { if (!current()) abort.abort(); });
    const timer = window.setTimeout(() => abort.abort(), 60000);
    try {
      const bytes = new TextEncoder().encode(text);
      if (bytes.length > 2 * 1024 * 1024 || text.includes("\0")) throw new Error("副本不能包含 NUL，且不能超过 2 MiB。");
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
      const verify = async () => {
        const [identity, result, saved] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getArtifact(project, artifact.id)]);
        const row = saved.artifact;
        if (!current() || identity.service_id !== artifact.service_id || result.session.id !== session || result.session.project_id !== project ||
            row.id !== artifact.id || row.project_id !== project || row.service_id !== artifact.service_id || row.snapshot_id !== artifact.snapshot_id ||
            row.sha256 !== artifact.sha256 || row.size_bytes !== artifact.size_bytes || row.version !== artifact.version || row.source_path !== artifact.source_path)
          throw new Error("服务、会话或来源成果已变化，请重新选择。");
      };
      await verify();
      const accepted = await requestConfirmation({ title: "保存编辑副本为项目附件？", description: `将编辑后的 ${filename} 保存到项目 ${project}。副本 SHA-256：${hash}，${bytes.length} 字节；原成果及源文件保持不变。`, confirmLabel: "保存附件" });
      if (!accepted || !current()) return;
      await verify();
      if (readPending()) throw new Error("已有待核对记录或浏览器存储不可用。");
      const record: Pending = { service: artifact.service_id, project, session, artifact: artifact.id, sourceHash: artifact.sha256, name: filename, sha256: hash, size: bytes.length };
      sessionStorage.setItem(key, JSON.stringify(record)); setPending(record);
      dispatched = true;
      const row = await uploadAttachment(new File([bytes], filename), project, artifact.service_id, base, abort.signal);
      await verify();
      if (row.project_id !== project || row.service_id !== artifact.service_id || !/^att_[a-f0-9]{32}$/.test(row.id) || row.sha256 !== hash || row.original_name !== filename || row.size_bytes !== bytes.length || !Number.isInteger(row.version) || row.version < 1 || JSON.stringify(readPending()) !== JSON.stringify(record)) throw new Error("附件回执不一致。");
      sessionStorage.removeItem(key); setPending(null);
      setNotice(`已保存附件 ${row.original_name} · v${row.version}（${row.id}），可在项目附件中查看。`);
    } catch (error) {
      if (scopeRef.current === snapshot) {
        setPending(readPending());
        setNotice(dispatched ? "保存结果未确认；请先在原服务和项目附件中按名称及 SHA-256 核对，避免重复保存。" : (error instanceof Error ? error.message : String(error)));
      }
    } finally {
      window.clearTimeout(timer); stop();
      if (operation.current === abort) { operation.current = null; setBusy(false); }
    }
  };
  const clear = async () => {
    if (!pending || operation.current) return;
    const expected = pending, snapshot = scope;
    const accepted = await requestConfirmation({ title: "清除待核对记录？", description: "请先在原服务和项目的附件列表核对。清除记录不会撤销可能已保存的附件。", confirmLabel: "已核对，清除记录" });
    if (!accepted || snapshot !== scopeRef.current || JSON.stringify(readPending()) !== JSON.stringify(expected)) return;
    try { sessionStorage.removeItem(key); setPending(null); setNotice("记录已清除，附件未被修改。"); }
    catch { setNotice("无法清除记录，请检查浏览器存储。"); }
  };
  return <div className="space-y-1 border-t border-line pt-2">
    <label className="block">附件名称<Input aria-label="编辑副本附件名称" value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={busy || !!pending || !project || !session || ipc.isMock || !validName(name)} onClick={() => void save()}>{busy ? "保存中…" : "保存编辑副本为项目附件"}</Button>
    {pending && <div role="alert" className="break-all"><p>上次保存结果待核对：{pending === "unavailable" ? "记录不可读取或浏览器存储不可用" : `服务 ${pending.service} · 项目 ${pending.project} · 会话 ${pending.session} · 来源 ${pending.artifact} · 来源 SHA-256 ${pending.sourceHash} · 名称 ${pending.name} · 副本 SHA-256 ${pending.sha256} · ${pending.size} 字节`}</p><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void clear()}>核对后清除记录</Button></div>}
    {notice && <p role="status" className="break-all">{notice}</p>}
  </div>;
}
