import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { Checkpoint } from "../../types/ipc";
import { Button, DiffLines } from "../../ui";

/** Review a captured checkpoint patch without applying it to the workspace. */
export function CheckpointPatchReview({ checkpoint, patch, legacyCwd }: { checkpoint: Checkpoint; patch: string; legacyCwd?: string }): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.currentProjectId);
  const host = ipc.agentBaseUrl;
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setSelected(null); setNote(""); setMessage(""); setBusy(false); return () => { ++epoch.current; }; }, [checkpoint.id, patch, legacyCwd, session, project, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setSelected(null); setNote(""); setMessage("连接变化，请重新打开检查点差异。"); setBusy(false); }), []);
  if (!patch.startsWith("diff --git ")) return null;
  const blocks = patch.split(/(?=^diff --git )/m).filter(Boolean);
  const append = async () => {
    if (lock.current || !selected || !note.trim() || !session || !project || checkpoint.session_id !== session) return;
    lock.current = true;
    const ticket = ++epoch.current, expected = selected;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    setBusy(true); setMessage("");
    try {
      const before = await typedIPC.getServiceIdentity();
      const [list, diff, sessionInfo] = await Promise.all([
        typedIPC.listCheckpoints(session),
        typedIPC.diffCheckpoint(checkpoint.id, checkpoint.source_root ? undefined : { cwd: legacyCwd!, legacy_source_confirmed: true }),
        typedIPC.getSession(session),
      ]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const row = list.checkpoints.find((item) => item.id === checkpoint.id);
      if (!before.service_id || before.service_id !== after.service_id || !row ||
          JSON.stringify(row) !== JSON.stringify(checkpoint) || sessionInfo.session.id !== session ||
          sessionInfo.session.project_id !== project || sessionInfo.session.archived ||
          diff.checkpoint_id !== checkpoint.id || !diff.available || diff.patch !== patch ||
          !diff.patch.split(/(?=^diff --git )/m).includes(expected))
        throw new Error("检查点、会话或服务已变化，请重新打开差异。");
      const excerpt = expected.slice(0, 12000);
      const text = `请根据以下审阅意见处理检查点记录（发送前可编辑）：\n检查点：${checkpoint.label} · ${checkpoint.id} · ${checkpoint.created_at}\n` +
        `来源目录：${checkpoint.source_root || legacyCwd}；这是检查点捕获的补丁，不是当前工作区差异或撤销预览。请先检查当前文件并保留用户同期修改。\n` +
        `审阅意见：\n${note.trim()}\n\n所选捕获补丁：\n${excerpt}` +
        (expected.length > excerpt.length ? "\n（仅附前 12000 个字符，请查看完整捕获补丁。）" : "") +
        "\n此操作未恢复文件或执行验证。";
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setSelected(null); setNote(""); }
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  return <div className="mt-2 space-y-2" aria-label="逐文件审阅检查点补丁">
    <p>选择捕获补丁中的文件，填写意见并追加到聊天草稿。不会应用补丁；当前文件可能已变化。</p>
    <div className="max-h-64 space-y-1 overflow-auto">{blocks.map((block, index) => <details key={index} className="rounded border border-line">
      <summary className="cursor-pointer break-all">{block.split("\n")[0]}</summary>
      <div className="overflow-auto"><DiffLines text={block} /></div>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => { setSelected(block); setNote(""); setMessage(""); }}>选择此文件补丁</Button>
    </details>)}</div>
    {selected && <div className="space-y-1 rounded border border-line p-2"><p className="break-all">已选：{selected.split("\n")[0]}</p>
      <label className="block">审阅意见或后续目标<textarea className="mt-1 block w-full rounded border border-line bg-surface-1 p-2" rows={3} maxLength={4000} disabled={busy} value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <Button size="sm" disabled={busy || !note.trim() || !project || checkpoint.session_id !== session} onClick={() => void append()}>追加检查点补丁到草稿</Button>{" "}
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setSelected(null); setNote(""); }}>取消</Button>
      <p>追加前重新核对记录与补丁；最多附前 12000 字符，请检查是否适合分享，不会自动发送。</p>
    </div>}
    {message && <p role="status" className="break-words">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
