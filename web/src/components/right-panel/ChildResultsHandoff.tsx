import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { Button } from "../../ui";

type Child = { run_id: string; session_id: string; parent_run_id: string; status: string; live: boolean;
  agent_id: string; agent_name: string; prompt: string; text: string; partial: boolean; error: string | null;
  text_truncated: boolean; created_at: string; completed_at: string | null };
type ChildList = { session_id: string; project_id: string | null; service_id: string; limit: number; children: Child[] };
const terminal = new Set(["completed", "failed", "cancelled", "interrupted", "budget_paused"]);
const clip = (value: string, limit: number) => value.length > limit ? `${value.slice(0, limit)}…（摘录已截断）` : value;

/** User-selected persisted child results, never an automatic merge or validation. */
export function ChildResultsHandoff({ sessionId }: { sessionId: string }): JSX.Element {
  const project = useSessionStore((s) => s.currentProjectId);
  const currentSession = useSessionStore((s) => s.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [snapshot, setSnapshot] = useState<ChildList | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setSnapshot(null); setSelected([]); setNote(""); setBusy(false); setMessage(""); return () => { ++epoch.current; }; }, [host, project, currentSession, sessionId]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setSnapshot(null); setSelected([]); setNote(""); setBusy(false); setMessage("连接变化，请重新读取子任务。"); }), []);
  const available = !!project && currentSession === sessionId;
  const read = async (ticket: number): Promise<ChildList> => {
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === project;
    const before = await typedIPC.getServiceIdentity();
    const [rows, session] = await Promise.all([
      ipc.request<ChildList>("run.child_list", { session_id: sessionId }), typedIPC.getSession(sessionId),
    ]);
    const after = await typedIPC.getServiceIdentity();
    if (!current() || !before.service_id || before.service_id !== after.service_id || rows.service_id !== before.service_id ||
        rows.session_id !== sessionId || rows.project_id !== project || session.session.id !== sessionId ||
        session.session.project_id !== project || session.session.archived || !Array.isArray(rows.children))
      throw new Error("服务、项目或会话归属已变化，请重新读取。");
    return rows;
  };
  const load = async () => {
    if (!available || lock.current) return;
    lock.current = true; const ticket = ++epoch.current; setBusy(true); setSnapshot(null); setSelected([]); setNote(""); setMessage("");
    try { const rows = await read(ticket); if (ticket === epoch.current) setSnapshot(rows); }
    catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  const append = async () => {
    if (!available || lock.current || !snapshot || !selected.length || !note.trim() || !project) return;
    lock.current = true; const ticket = ++epoch.current; setBusy(true); setMessage("");
    try {
      const fresh = await read(ticket);
      if (ticket !== epoch.current) return;
      if (fresh.service_id !== snapshot.service_id) throw new Error("服务身份已变化，请重新读取。");
      const chosen = selected.map((id) => {
        const old = snapshot.children.find((row) => row.run_id === id), next = fresh.children.find((row) => row.run_id === id);
        if (!old || !next || !terminal.has(next.status) || next.live || JSON.stringify(old) !== JSON.stringify(next))
          throw new Error("所选子任务记录已变化，请重新读取并核对。");
        return next;
      });
      const items = chosen.map((row) => `子任务：${row.agent_name || row.agent_id || "未命名"} · ${row.run_id} · 子会话 ${row.session_id} · 父运行 ${row.parent_run_id}\n` +
        `持久状态：${row.status}${row.partial ? " · 部分结果，需核对" : ""}\n` +
        `任务要求（摘录）：${clip(row.prompt, 1000) || "（未记录）"}\n` +
        `结果文本（未经核实）：\n${clip(row.text, 2000) || "（没有保存的文本）"}` +
        (row.text_truncated ? "\n（服务端结果文本已截断）" : "") +
        (row.error ? `\n错误（摘录）：${clip(row.error, 1000)}` : ""));
      const text = `请结合以下子任务记录处理后续目标（发送前可编辑）：\n父会话：${sessionId}\n后续目标：${note.trim()}\n\n${items.join("\n\n")}\n\n文本和状态不代表文件已合并或验证通过；请审阅各子工作区、实际文件及原运行验证记录，保留用户同期修改。`;
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session: sessionId, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setSnapshot(null); setSelected([]); setNote(""); }
    } catch (error) { if (ticket === epoch.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (ticket === epoch.current) setBusy(false); }
  };
  const rows = snapshot?.children.filter((row) => terminal.has(row.status) && !row.live) ?? [];
  return <div className="mb-2 space-y-2 rounded border border-line p-2 text-xs" aria-label="子任务结果交接">
    <Button size="sm" variant="secondary" disabled={!available || busy} onClick={() => void load()}>读取子任务并准备交接</Button>
    {!available && <p>请先切换到所属项目会话。</p>}
    {snapshot && <><p>最多选择 5 个已结束且未发现运行中的子任务。仅显示最近 {snapshot.limit} 条记录；状态与文本不证明验证或交付。</p>
      {!rows.length && <p>本次读取没有可选择的已结束子任务。</p>}
      <div className="max-h-64 space-y-1 overflow-auto">{rows.map((row) => <details key={row.run_id} className="rounded border border-line p-1">
        <summary className="break-all">{row.agent_name || row.agent_id || "子任务"} · {row.status}{row.partial ? " · 部分结果" : ""} · {row.run_id}</summary>
        <p>要求：{clip(row.prompt, 1000) || "（未记录）"}</p><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all">{clip(row.text, 2000) || "（没有保存的文本）"}</pre>
        {row.text_truncated && <p>服务端结果文本已截断。</p>}{row.error && <p className="break-all">错误：{clip(row.error, 1000)}</p>}
        <label><input type="checkbox" checked={selected.includes(row.run_id)} disabled={busy || (!selected.includes(row.run_id) && selected.length >= 5)} onChange={(event) => { const checked = event.target.checked; setSelected((ids) => checked ? [...ids, row.run_id] : ids.filter((id) => id !== row.run_id)); }} /> 加入交接</label>
      </details>)}</div>
      {selected.length > 0 && <><label className="block">后续目标<textarea value={note} disabled={busy} maxLength={4000} onChange={(event) => setNote(event.target.value)} className="mt-1 block w-full rounded border border-line bg-surface-1 p-2" rows={3} /></label>
        <Button size="sm" disabled={busy || !note.trim()} onClick={() => void append()}>将所选结果追加到草稿</Button>
        <p>追加前重新核对记录；每项结果最多附前 2000 字符。请检查是否适合分享；不会自动发送或合并。</p></>}
    </>}
    {message && <p role="status" className="break-words">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
