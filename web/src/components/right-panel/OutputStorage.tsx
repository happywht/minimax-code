import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { StreamEvent } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";

interface OutputItem { output_ref: string; run_id: string; session_id: string; tool_name: string; bytes: number; sha256: string; created_at: string; run_status: string; cleared_at: string | null }
interface Inventory { session_id: string; project_id: string | null; service_id: string; items: OutputItem[]; total_bytes: number; session_bytes: number; limit_bytes: number; offset: number; has_more: boolean }
const size = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function OutputStorage(): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.sessions.find((row) => row.id === session)?.project_id ?? null);
  const [open, setOpen] = useState(false), [data, setData] = useState<Inventory | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const sameScope = () => {
    const state = useSessionStore.getState();
    return state.currentSessionId === session && (state.sessions.find((row) => row.id === session)?.project_id ?? null) === project;
  };
  useEffect(() => {
    setOpen(false); setData(null); setError(""); setBusy(false);
    const version = epoch;
    return () => { version.current++; };
  }, [session, project]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setData(null); setBusy(false); setError("连接变化，请重新查看存储用量。"); }), []);
  useEffect(() => ipc.on<{ session_id: string; service_id: string; output_ref: string; cleared_at: string }>(StreamEvent.RunOutputCleared, (event) => {
    const change = event.data;
    if (!change || change.session_id !== session || !data || data.service_id !== change.service_id) return;
    epoch.current++; setBusy(false);
    setData((current) => {
      if (!current || current.service_id !== change.service_id) return current;
      const item = current.items.find((row) => row.output_ref === change.output_ref);
      if (!item || item.cleared_at) return current;
      return { ...current, total_bytes: Math.max(0, current.total_bytes - item.bytes),
        session_bytes: Math.max(0, current.session_bytes - item.bytes),
        items: current.items.map((row) => row.output_ref === item.output_ref ? { ...row, cleared_at: change.cleared_at } : row) };
    });
  }), [session, data]);
  const load = async (offset: number) => {
    if (!session) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setError("");
    try {
      const next = await ipc.request<Inventory>("run.output_inventory", { session_id: session, offset });
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl || !sameScope()) return;
      if (next.session_id !== session || next.project_id !== project || !next.service_id || next.offset !== offset ||
          next.items.some((item) => item.session_id !== session)) throw new Error("存储归属已变化");
      setData(next);
    } catch (failure) { if (ticket === epoch.current && sameScope()) { setData(null); setError(String(failure)); } }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const clear = async (item: OutputItem) => {
    if (!data || !session || busy) return;
    const ticket = epoch.current, host = ipc.agentBaseUrl;
    const accepted = await requestConfirmation({ title: "清理已捕获输出？",
      description: `清理 ${item.tool_name} 的 ${size(item.bytes)} 输出后无法在应用中恢复。任务与摘要保留；已有备份可能仍含原内容。`, confirmLabel: "清理此输出" });
    if (!accepted || ticket !== epoch.current || host !== ipc.agentBaseUrl || !sameScope()) return;
    setBusy(true); setError("");
    try {
      const result = await ipc.request<{ output_ref: string; run_id: string; session_id: string; service_id: string }>("run.output_clear", {
        run_id: item.run_id, session_id: session, output_ref: item.output_ref,
        expected_service_id: data.service_id, expected_sha256: item.sha256 });
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl || !sameScope()) return;
      if (result.output_ref !== item.output_ref || result.run_id !== item.run_id || result.session_id !== session || result.service_id !== data.service_id) throw new Error("清理回执归属不一致");
      await load(data.offset);
    } catch (failure) { if (ticket === epoch.current && sameScope()) setError(String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  if (!session) return null;
  return <section className="border-b border-minimax-border px-3 py-2 text-xs" data-testid="output-storage">
    <button type="button" onClick={() => { setOpen((old) => !old); if (!open) void load(0); }}>
      {open ? "收起工具输出存储" : "工具输出存储与清理"}</button>
    {open && <div className="mt-1 space-y-1">
      {error && <p role="alert">{error}</p>}
      {data && <><p>全库：{size(data.total_bytes)} / {size(data.limit_bytes)}；当前会话：{size(data.session_bytes)}。</p>
        <p>只列当前会话，切换会话可查看其记录。清理不会删除任务、摘要或原文件；不会自动清理。</p>
        {data.items.length === 0 ? <p>本页没有记录。</p> : data.items.map((item) => <div key={item.output_ref} className="rounded border border-minimax-border p-1">
          <p>{item.tool_name} · {item.created_at} · {size(item.bytes)} · 运行 {item.run_id}</p>
          <p>{item.cleared_at ? "已清理，无法再读取" : `运行状态：${item.run_status}`}</p>
          <button type="button" disabled={busy || !!item.cleared_at || !terminal.has(item.run_status)} onClick={() => void clear(item)}>清理此捕获输出</button>
        </div>)}
        <button type="button" disabled={busy || data.offset === 0} onClick={() => void load(Math.max(0, data.offset - 50))}>上一页</button>{" "}
        <button type="button" disabled={busy || !data.has_more} onClick={() => void load(data.offset + 50)}>下一页</button>{" "}
        <button type="button" disabled={busy} onClick={() => void load(data.offset)}>刷新用量</button>
        <p>这是逻辑用量；SQLite 文件和旧备份可能仍占空间或含有原内容。</p>
      </>}
    </div>}
  </section>;
}
