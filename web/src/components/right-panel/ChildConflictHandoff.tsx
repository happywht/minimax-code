import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { ChildWorkspaceReview } from "../../types/ipc";
import { Button } from "../../ui";

/** Prepare a review handoff; this component does not modify either workspace. */
export function ChildConflictHandoff({ review, teamRunId, disabled }: { review: ChildWorkspaceReview; teamRunId?: string; disabled: boolean }): JSX.Element | null {
  const project = useSessionStore((s) => s.currentProjectId);
  const session = useSessionStore((s) => s.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setSelected([]); setNote(""); setBusy(false); setMessage(""); return () => { ++epoch.current; }; }, [host, project, session, review.run_id, review.revision, review.service_id, teamRunId]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setSelected([]); setNote(""); setBusy(false); setMessage("连接变化，请重新查看工作区。"); }), []);
  const conflicts = review.files.filter((file) => !!file.conflict);
  if (!conflicts.length) return null;
  const available = !!project && project === review.project_id && session === review.session_id;
  const append = async () => {
    if (!available || !project || !session || !selected.length || !note.trim() || lock.current || disabled) return;
    lock.current = true; const ticket = ++epoch.current; setBusy(true); setMessage("");
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    try {
      const fresh = await ipc.request<ChildWorkspaceReview>("run.child_workspace", {
        run_id: review.run_id, session_id: session, action: "preview", ...(teamRunId ? { team_run_id: teamRunId } : {}),
      });
      if (!current()) return;
      if (fresh.run_id !== review.run_id || fresh.session_id !== session || fresh.project_id !== project ||
          fresh.service_id !== review.service_id || fresh.root !== review.root || fresh.kind !== review.kind ||
          fresh.revision !== review.revision || !Array.isArray(fresh.files)) throw new Error("工作区或审阅版本已变化，请重新查看。");
      const files = selected.map((path) => {
        const old = review.files.find((file) => file.path === path), next = fresh.files.find((file) => file.path === path);
        if (!old || !next || !next.conflict || JSON.stringify(old) !== JSON.stringify(next)) throw new Error("所选冲突已变化，请重新查看并审阅。");
        return next;
      });
      let remaining = 12000;
      const parts = files.map((file) => {
        const value = file.diff ?? "", excerpt = value.slice(0, Math.min(4000, remaining)); remaining -= excerpt.length;
        return `文件：${file.path}\n冲突：${file.conflict}\n父文件 SHA-256：${file.parent_sha256 ?? "未知或不存在"}\n子文件 SHA-256：${file.child_sha256 ?? "未知或不存在"}\n` +
          (file.diff_unavailable || typeof file.diff !== "string" ? "差异不可用，请直接检查文件。" : `父子差异摘录：\n${excerpt || (value ? "（总长度上限，未附差异）" : "（无文本差异）")}`) +
          (file.diff_truncated ? "\n（服务端差异已截断）" : "") + (value.length > excerpt.length ? "\n（草稿差异未完整加入）" : "");
      });
      const text = `请处理以下子工作区冲突（发送前可编辑）：\n子任务：${review.run_id}${teamRunId ? ` · 团队运行：${teamRunId}` : ""}\n父目录：${review.root}\n审阅意见：${note.trim()}\n\n${parts.join("\n\n")}\n\n这些是审阅时的父子差异，不代表已合并或验证通过；先核对当前文件和完整差异，保留用户同期修改。`;
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setSelected([]); setNote(""); }
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2" aria-label="多文件冲突交接">
    <p>最多选择 5 项冲突并填写目标。仅准备草稿，不修改文件；追加前重查审阅版本。</p>
    {!available && <p>请先切换到所属项目会话。</p>}
    <ul className="max-h-40 overflow-auto">{conflicts.map((file) => <li key={file.path} className="break-all"><label><input type="checkbox" checked={selected.includes(file.path)} disabled={disabled || busy || (!selected.includes(file.path) && selected.length >= 5)} onChange={(event) => { const checked = event.target.checked; setSelected((items) => checked ? [...items, file.path] : items.filter((path) => path !== file.path)); }} /> {file.path} · {file.conflict}</label></li>)}</ul>
    {selected.length > 0 && <><label className="block">后续目标或审阅意见<textarea className="mt-1 block w-full rounded border border-line bg-surface-0 p-2" rows={3} maxLength={4000} disabled={disabled || busy} value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <Button size="sm" disabled={!available || disabled || busy || !note.trim()} onClick={() => void append()}>将所选冲突追加到草稿</Button>
      <p>每项最多 4000 字，合计 12000 字；截断或不可用会标明。请检查分享内容，不会自动发送。</p></>}
    {message && <p role="status" className="break-words">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
