import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchArtifactContent } from "../../ipc/artifactContent";
import { useSessionStore } from "../../stores/sessionStore";
import type { TextArtifact } from "../../types/ipc";
import { RunArtifactFollowup } from "./RunArtifactFollowup";

/** Explicitly registered run links only; registration does not prove authorship. */
export function RunTextArtifacts({ runId, sessionId, projectId, serviceId, review = false, onPrepareReport }: { runId: string; sessionId: string; projectId: string; serviceId: string; review?: boolean; onPrepareReport?: (row: TextArtifact) => void }): JSX.Element {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [items, setItems] = useState<TextArtifact[] | null>(null);
  const [selected, setSelected] = useState<{ row: TextArtifact; preview: string } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const [candidates, setCandidates] = useState<string[] | null>(null);
  const key = `run-text-registration:${JSON.stringify([host, serviceId, projectId, sessionId, runId])}`;
  const [pending, setPending] = useState<{ path: string; requestId: string } | null>(null);
  const epoch = useRef(0), download = useRef<AbortController | null>(null);
  useEffect(() => {
    ++epoch.current; download.current?.abort(); setItems(null); setSelected(null); setMessage(""); setBusy(false);
    setPath(""); setCandidates(null);
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || "null");
      setPending(saved && typeof saved.path === "string" && /^[0-9a-f]{32}$/.test(saved.requestId) ? saved : null);
    } catch { setPending(null); }
    return () => { ++epoch.current; download.current?.abort(); };
  }, [runId, sessionId, projectId, serviceId, review, activeSession, host, key]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; download.current?.abort(); setItems(null); setSelected(null); setCandidates(null); setBusy(false); setMessage("连接变化，请重新读取成果。"); }), []);
  const current = (ticket: number, host: string) => ticket === epoch.current && host === ipc.agentBaseUrl &&
    useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === projectId &&
    (review || (activeSession === sessionId && useSessionStore.getState().sessions.find((s) => s.id === sessionId)?.project_id === projectId));
  // The dashboard supplies a previously verified run; recheck its binding around each read.
  const verifyScope = async (ticket: number, origin: string) => {
    if (!review) return;
    const scope = await typedIPC.getProjectRunSummary({ project_id: projectId, expected_service_id: serviceId, run_id: runId });
    if (!current(ticket, origin)) throw new Error("查看范围已变化");
    if (scope.project_id !== projectId || scope.service_id !== serviceId || scope.detail?.id !== runId || scope.detail.session_id !== sessionId)
      throw new Error("运行或会话归属已改变，请刷新任务。");
  };
  const load = async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl; setBusy(true); setMessage(""); setSelected(null);
    try {
      await verifyScope(ticket, host);
      const result = await typedIPC.listArtifacts(projectId, runId);
      await verifyScope(ticket, host);
      if (!current(ticket, host)) return;
      if (result.service_id !== serviceId) throw new Error("服务身份已变化");
      setItems(result.artifacts.filter((row) => row.project_id === projectId && row.service_id === serviceId && row.run_id === runId));
      if (result.artifacts.length >= 200) setMessage("此运行最多返回最近 200 项，可能还有更早的关联成果。");
    } catch (error) { if (current(ticket, host)) { setItems(null); setMessage(String(error)); } }
    finally { if (current(ticket, host)) setBusy(false); }
  };
  const discover = async () => {
    const ticket = ++epoch.current, origin = ipc.agentBaseUrl; setBusy(true); setMessage(""); setCandidates(null);
    try {
      await verifyScope(ticket, origin);
      const result = await typedIPC.getArtifactCandidates({ project_id: projectId, run_id: runId,
        expected_session_id: sessionId, expected_service_id: serviceId });
      await verifyScope(ticket, origin);
      if (!current(ticket, origin)) return;
      if (result.project_id !== projectId || result.run_id !== runId || result.session_id !== sessionId || result.service_id !== serviceId)
        throw new Error("候选范围已变化，请刷新任务。");
      setCandidates(result.paths);
      setMessage(result.truncated ? "仅检查最近 200 条成功写入记录；更早路径可手工输入。" : "候选来自成功写入记录，请确认当前文件后登记。");
    } catch (error) { if (current(ticket, origin)) setMessage(String(error)); }
    finally { if (current(ticket, origin)) setBusy(false); }
  };
  const register = async () => {
    const ticket = ++epoch.current, origin = ipc.agentBaseUrl;
    const request = pending ?? { path: path.trim(), requestId: crypto.randomUUID().replaceAll("-", "") };
    if (!request.path || !current(ticket, origin)) return;
    setBusy(true); setMessage("");
    try {
      // Persist before issuing the request: an unknown response must be retried with the same ID.
      sessionStorage.setItem(key, JSON.stringify(request));
      setPending(request);
      await verifyScope(ticket, origin);
      const identity = await typedIPC.getServiceIdentity();
      if (!current(ticket, origin) || identity.service_id !== serviceId) throw new Error("服务或查看范围已变化");
      const result = await typedIPC.registerArtifact({ project_id: projectId, source_path: request.path,
        run_id: runId, expected_service_id: serviceId, expected_session_id: sessionId, request_id: request.requestId });
      await verifyScope(ticket, origin);
      const after = await typedIPC.getServiceIdentity();
      const row = result.artifact;
      if (!current(ticket, origin) || after.service_id !== serviceId || row.service_id !== serviceId ||
          row.project_id !== projectId || row.run_id !== runId || row.source_path !== request.path || row.id !== `art_${request.requestId}`)
        throw new Error("登记结果的范围已变化，请使用同一请求重试核对。" );
      sessionStorage.removeItem(key); setPending(null); setPath("");
      setItems((previous) => [row, ...(previous ?? []).filter((item) => item.id !== row.id)]);
      setMessage("已登记关联的文本快照；请自行核对内容和质量。");
    } catch (error) { if (current(ticket, origin)) setMessage(`登记结果可能未知，请使用同一请求重试核对：${String(error)}`); }
    finally { if (current(ticket, origin)) setBusy(false); }
  };
  const select = async (row: TextArtifact) => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl; setBusy(true); setMessage(""); setSelected(null);
    try {
      await verifyScope(ticket, host);
      const result = await typedIPC.getArtifact(projectId, row.id);
      await verifyScope(ticket, host);
      if (!current(ticket, host)) return;
      const file = result.artifact;
      if (file.id !== row.id || file.project_id !== projectId || file.service_id !== serviceId || file.run_id !== runId || file.sha256 !== row.sha256)
        throw new Error("文本成果关联或字节标识已变化，请刷新。");
      setSelected({ row: file, preview: result.preview_text });
    } catch (error) { if (current(ticket, host)) setMessage(String(error)); }
    finally { if (current(ticket, host)) setBusy(false); }
  };
  const save = async () => {
    if (!selected) return;
    const ticket = epoch.current, host = ipc.agentBaseUrl, row = selected.row;
    const controller = new AbortController(); download.current?.abort(); download.current = controller; setBusy(true); setMessage("");
    try {
      await verifyScope(ticket, host);
      const blob = await fetchArtifactContent(row, host, controller.signal);
      await verifyScope(ticket, host);
      if (!current(ticket, host)) return;
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = row.source_path.split(/[\\/]/).pop() || "artifact.txt"; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (current(ticket, host)) setMessage(String(error)); }
    finally { if (current(ticket, host)) setBusy(false); }
  };
  return <div className="mt-2 space-y-1 text-xs"><button type="button" disabled={busy} onClick={() => void load()}>读取登记关联的文本成果</button>
    <p>仅显示登记时关联此运行的文本版本；关联不证明由该运行生成或内容已验证。</p>
    <button type="button" disabled={busy || !!pending} onClick={() => void discover()}>查找任务写入的文本路径</button>
    {candidates && (candidates.length ? <ul>{candidates.map((candidate) => <li key={candidate}><button type="button" disabled={busy || !!pending} onClick={() => setPath(candidate)}>{candidate}</button></li>)}</ul> : <p>未发现可用候选，可手工输入路径。</p>)}
    <p>候选仅来自运行记录，文件可能已变化或不在项目中；登记快照及关联不代表来源或业务验收。</p>
    <label className="block">项目内文本路径（TXT / MD / CSV，UTF-8，最多 2 MiB）
      <input className="ml-2 rounded border border-line bg-surface-0 p-1" value={pending?.path ?? path} disabled={busy || !!pending} onChange={(event) => setPath(event.target.value)} />
    </label>
    <button type="button" disabled={busy || !(pending?.path ?? path).trim()} onClick={() => void register()}>
      {pending ? "用同一请求重试登记" : "登记并关联此运行"}
    </button>
    {pending && <div><p>登记结果尚未确认。重试使用相同请求，避免重复创建；路径和文件内容须保持不变。</p>
      <button type="button" disabled={busy} onClick={() => { sessionStorage.removeItem(key); setPending(null); setMessage("已清除本地重试记录；原请求可能已登记，请先读取成果确认，避免重复。"); }}>清除本地重试记录</button>
    </div>}
    {items && (items.length ? <ul>{items.map((row) => <li key={row.id}><button type="button" disabled={busy} onClick={() => void select(row)}>{row.source_path} · v{row.version}</button></li>)}</ul> : <p>此运行没有登记的文本关联。</p>)}
    {selected && <div><button type="button" disabled={busy} onClick={() => void save()}>下载文本原件</button>{onPrepareReport && <button type="button" disabled={busy} onClick={() => onPrepareReport(selected.row)}>在报告中复用此成果</button>}<pre aria-label="文本成果预览" className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{selected.preview || "（无可用预览）"}</pre>
      {!review && <RunArtifactFollowup key={`${selected.row.id}:${selected.row.sha256}`} artifact={selected.row} session={sessionId} />}</div>}
    {message && <p role="status">{message}</p>}
  </div>;
}
