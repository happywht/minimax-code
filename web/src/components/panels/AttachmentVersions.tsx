import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";

type Preview = { attachment: AttachmentOriginal; text: string };
export function AttachmentVersions({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element {
  const [rows, setRows] = useState<AttachmentOriginal[]>([]), [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [comparison, setComparison] = useState<[Preview, Preview] | null>(null);
  const epoch = useRef(0);
  useEffect(() => {
    const invalidate = () => { epoch.current++; setRows([]); setCursor(null); setLoaded(false); setBusy(false); setComparison(null); };
    invalidate(); setError("");
    const off = ipc.onSideCar(invalidate);
    return () => { epoch.current++; off(); };
  }, [attachment.id, attachment.sha256, project, session, service, host]);
  const current = (ticket: number) => {
    const active = useSessionStore.getState(), store = useAttachmentStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && active.currentProjectId === project && active.currentSessionId === session &&
      store.projectId === project && store.host === host && store.serviceId === service && store.selected?.id === attachment.id && store.selected.sha256 === attachment.sha256;
  };
  const list = async (next: string | null) => {
    if (!project || !service || busy) return;
    const ticket = ++epoch.current; setBusy(true); setError("");
    try {
      const result = await typedIPC.listAttachments(project, next ?? undefined, attachment.original_name);
      if (!current(ticket)) return;
      if (result.service_id !== service || result.attachments.length > 200 || result.attachments.some((row) => row.project_id !== project || row.service_id !== service) ||
          (result.next_cursor != null && (!result.next_cursor.startsWith("q_") || result.next_cursor === next))) throw new Error("版本列表归属或游标无效");
      const matches = result.attachments.filter((row) => row.original_name === attachment.original_name);
      setRows((previous) => {
        const existing = new Set(next ? previous.map((row) => row.id) : []);
        return [...(next ? previous : []), ...matches.filter((row) => !existing.has(row.id))];
      });
      setCursor(result.next_cursor ?? null); setLoaded(true);
      if (!next) setComparison(null);
    } catch (reason) { if (current(ticket)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const compare = async (other: AttachmentOriginal) => {
    if (!project || !service || busy) return;
    const ticket = ++epoch.current; setBusy(true); setError(""); setComparison(null);
    try {
      const previews: Preview[] = [];
      for (const expected of [attachment, other]) {
        const result = await typedIPC.getAttachment(project, expected.id);
        if (!current(ticket)) return;
        const row = result.attachment;
        if (row.project_id !== project || row.service_id !== service || row.id !== expected.id || row.sha256 !== expected.sha256 ||
            row.original_name !== attachment.original_name || row.version !== expected.version || typeof result.preview_text !== "string" ||
            row.quality_status !== "unverified" || typeof row.preview_truncated !== "boolean" ||
            new TextEncoder().encode(result.preview_text).length > 65536) throw new Error("版本身份或预览不匹配，请重新读取列表");
        previews.push({ attachment: row, text: ["parsed", "empty", "truncated"].includes(row.parser_status) ? result.preview_text : "" });
      }
      setComparison([previews[0], previews[1]]);
    } catch (reason) { if (current(ticket)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <h4 className="font-medium">同名附件版本</h4>
    <p>按当前项目中完全相同的文件名查找；同名版本不保证内容或来源有继承关系。比较仅展示有界解析预览，不等于完整文件或质量核验。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !service} onClick={() => void list(null)}>查找同名版本</Button>
    {loaded && <><p>已加载 {rows.length} 个同名版本{cursor ? "，仍可能有更早版本" : ""}。</p>
      {rows.map((row) => <div key={row.id} className="break-all rounded border border-line p-2">
        <p>v{row.version} · {row.id} · {row.size_bytes} 字节 · {row.created_at} · {row.parser_status} / 未验证</p>
        <p className="text-xs">SHA-256 {row.sha256}{row.id === attachment.id ? " · 当前版本" : ""}</p>
        {row.id !== attachment.id && <Button type="button" variant="secondary" disabled={busy} onClick={() => void compare(row)}>对照当前版本</Button>}
      </div>)}
      {cursor && <Button type="button" variant="secondary" disabled={busy} onClick={() => void list(cursor)}>继续查找更早版本</Button>}</>}
    {comparison && <div className="space-y-2"><p>仅作并排预览；未计算差异，缺失或截断的文本不代表原件没有内容。</p>
      <div className="grid gap-2 md:grid-cols-2">{comparison.map((item) => <div key={item.attachment.id}><p>v{item.attachment.version} · {item.attachment.id} · {item.attachment.parser_status} · 范围 {item.attachment.parser_scope}{item.attachment.preview_truncated || item.attachment.parser_status === "truncated" ? " · 预览截断" : ""}</p>
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words">{item.text || "（无可用文本预览）"}</pre></div>)}</div>
      <Button type="button" variant="secondary" disabled={busy} onClick={() => {
        const target = comparison[1].attachment;
        if (!current(epoch.current)) { setError("上下文已变化，请重新查找版本"); return; }
        void useAttachmentStore.getState().select(target.id, target.sha256);
      }}>打开对照版本继续处理</Button></div>}
    {busy && <p role="status">正在读取…</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
