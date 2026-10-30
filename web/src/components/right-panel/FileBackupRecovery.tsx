import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { FileBackupItem, FileBackupList } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";

export function FileBackupRecovery(): JSX.Element {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.currentProjectId);
  const [list, setList] = useState<FileBackupList | null>(null);
  const [selected, setSelected] = useState<FileBackupItem | null>(null);
  const [destination, setDestination] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  useEffect(() => {
    ++epoch.current; setList(null); setSelected(null); setDestination(""); setMessage(""); setBusy(false);
  }, [session, project]);
  useEffect(() => ipc.onSideCar(() => {
    ++epoch.current; setList(null); setSelected(null); setBusy(false);
    setMessage("连接已变化，请重新查看备份。");
  }), []);

  const load = async () => {
    if (!session || !project || busy) return;
    const ticket = ++epoch.current;
    setBusy(true); setList(null); setSelected(null); setMessage("");
    try {
      const { service_id } = await typedIPC.getServiceIdentity();
      const result = await typedIPC.listFileBackups({ session_id: session, project_id: project, expected_service_id: service_id });
      if (epoch.current === ticket) setList(result);
    } catch (error) {
      if (epoch.current === ticket) setMessage(`无法读取备份：${String(error)}`);
    } finally { if (epoch.current === ticket) setBusy(false); }
  };
  const restore = async () => {
    if (!session || !project || !list || !selected || !destination.trim() || busy) return;
    const ticket = epoch.current, path = destination.trim(), item = selected, snapshot = list;
    setBusy(true);
    try {
      const accepted = await requestConfirmation({ title: "恢复文件备份副本", confirmLabel: "创建新文件",
        description: `从 ${item.original_path} 的备份创建 ${path}。目标必须不存在，父目录必须已存在；SHA-256：${item.sha256}。` });
      if (!accepted || epoch.current !== ticket || useSessionStore.getState().currentSessionId !== session ||
          useSessionStore.getState().currentProjectId !== project) return;
      const result = await typedIPC.restoreFileBackup({ session_id: session, project_id: project,
        expected_service_id: snapshot.service_id, workspace: snapshot.workspace, workspace_identity: snapshot.workspace_identity,
        backup_id: item.id, expected_sha256: item.sha256, destination: path, confirmed: true });
      if (epoch.current === ticket) {
        if (result.backup_id !== item.id || result.sha256 !== item.sha256 || result.destination !== path) throw new Error("恢复回执不匹配");
        setMessage(`已创建备份副本：${path}`); setSelected(null); setDestination("");
      }
    } catch (error) {
      if (epoch.current === ticket) { setList(null); setSelected(null); setMessage(`恢复结果未确认，请先检查目标文件，勿自动重试：${String(error)}`); }
    } finally { if (epoch.current === ticket) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p className="font-medium">文件编辑备份</p>
    <p>仅显示当前会话工作区中带清单且摘要匹配的原生文件编辑备份；不涵盖全部备份来源。摘要匹配不证明来源可信。</p>
    <button type="button" disabled={!session || !project || busy} onClick={() => void load()}>查看可用备份</button>
    {list && <><p className="break-all">工作区：{list.workspace}</p>
      {list.items.length === 0 ? <p>未找到可用备份。</p> : <ul className="space-y-1">{list.items.map((item) => <li key={item.id} className="break-all">
        <button type="button" disabled={busy} onClick={() => { setSelected(item); setDestination(""); }}>{item.original_path} · {item.timestamp} · {item.bytes} 字节</button>
      </li>)}</ul>}
      {list.truncated && <p>仅检查最近的 500 份清单。</p>}</>}
    {selected && <div className="space-y-1"><p className="break-all">已选择：{selected.original_path} · SHA-256 {selected.sha256}</p>
      <label className="block">恢复到工作区内的新路径（父目录须已存在）<input className="w-full rounded border border-line bg-surface-0 p-1" value={destination} disabled={busy} onChange={(e) => setDestination(e.target.value)} /></label>
      <button type="button" disabled={busy || !destination.trim()} onClick={() => void restore()}>审阅并创建副本</button></div>}
    {message && <p role="status" className="break-all">{message}</p>}
  </div>;
}
