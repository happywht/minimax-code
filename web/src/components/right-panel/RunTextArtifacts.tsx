import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchArtifactContent } from "../../ipc/artifactContent";
import { useSessionStore } from "../../stores/sessionStore";
import type { TextArtifact } from "../../types/ipc";

/** Explicitly registered run links only; registration does not prove authorship. */
export function RunTextArtifacts({ runId, sessionId, projectId, serviceId }: { runId: string; sessionId: string; projectId: string; serviceId: string }): JSX.Element {
  const [items, setItems] = useState<TextArtifact[] | null>(null);
  const [selected, setSelected] = useState<{ row: TextArtifact; preview: string } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0), download = useRef<AbortController | null>(null);
  useEffect(() => {
    ++epoch.current; download.current?.abort(); setItems(null); setSelected(null); setMessage(""); setBusy(false);
    return () => { ++epoch.current; download.current?.abort(); };
  }, [runId, sessionId, projectId, serviceId]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; download.current?.abort(); setItems(null); setSelected(null); setBusy(false); setMessage("连接变化，请重新读取成果。"); }), []);
  const current = (ticket: number, host: string) => ticket === epoch.current && host === ipc.agentBaseUrl &&
    useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === projectId &&
    useSessionStore.getState().sessions.find((s) => s.id === sessionId)?.project_id === projectId;
  const load = async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl; setBusy(true); setMessage(""); setSelected(null);
    try {
      const result = await typedIPC.listArtifacts(projectId, runId);
      if (!current(ticket, host)) return;
      if (result.service_id !== serviceId) throw new Error("服务身份已变化");
      setItems(result.artifacts.filter((row) => row.project_id === projectId && row.service_id === serviceId && row.run_id === runId));
      if (result.artifacts.length >= 200) setMessage("此运行最多返回最近 200 项，可能还有更早的关联成果。");
    } catch (error) { if (current(ticket, host)) { setItems(null); setMessage(String(error)); } }
    finally { if (current(ticket, host)) setBusy(false); }
  };
  const select = async (row: TextArtifact) => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl; setBusy(true); setMessage(""); setSelected(null);
    try {
      const result = await typedIPC.getArtifact(projectId, row.id);
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
      const blob = await fetchArtifactContent(row, host, controller.signal);
      if (!current(ticket, host)) return;
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = row.source_path.split(/[\\/]/).pop() || "artifact.txt"; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (current(ticket, host)) setMessage(String(error)); }
    finally { if (current(ticket, host)) setBusy(false); }
  };
  return <div className="mt-2 space-y-1 text-xs"><button type="button" disabled={busy} onClick={() => void load()}>读取登记关联的文本成果</button>
    <p>仅显示登记时关联此运行的文本版本；关联不证明由该运行生成或内容已验证。</p>
    {items && (items.length ? <ul>{items.map((row) => <li key={row.id}><button type="button" disabled={busy} onClick={() => void select(row)}>{row.source_path} · v{row.version}</button></li>)}</ul> : <p>此运行没有登记的文本关联。</p>)}
    {selected && <div><button type="button" disabled={busy} onClick={() => void save()}>下载文本原件</button><pre aria-label="文本成果预览" className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{selected.preview || "（无可用预览）"}</pre></div>}
    {message && <p role="status">{message}</p>}
  </div>;
}
