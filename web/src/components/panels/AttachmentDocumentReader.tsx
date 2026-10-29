import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button, Input } from "../../ui";
import type { AttachmentDocumentCursor, AttachmentDocumentResult, AttachmentOriginal } from "../../types/ipc";

export function AttachmentDocumentReader({ attachment, project, session, service, host, onInsertReport }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
  onInsertReport?: (text: string, location: string) => void;
}): JSX.Element | null {
  const [result, setResult] = useState<AttachmentDocumentResult | null>(null);
  const [unit, setUnit] = useState("1"), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [searchCursor, setSearchCursor] = useState<(AttachmentDocumentCursor & { skipBefore?: boolean }) | null>(null);
  const [matches, setMatches] = useState<{ unit: number; location: string; start: number; end: number; snippet: string }[]>([]);
  const [searched, setSearched] = useState(false);
  const epoch = useRef(0);
  const eligible = (attachment.recognized_type === "pdf" || attachment.recognized_type === "docx") &&
    attachment.declared_type === attachment.recognized_type;
  useEffect(() => {
    ++epoch.current; setResult(null); setError(""); setBusy(false); setUnit("1"); setQuery(""); setMatches([]); setSearchCursor(null); setSearched(false);
    return () => { ++epoch.current; };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setResult(null); setBusy(false); setError("连接变化，请重新选择附件。"); setMatches([]); setSearchCursor(null); setSearched(false); }), []);
  if (!eligible) return null;
  const read = async (cursor: AttachmentDocumentCursor) => {
    if (!project || !session || !service || busy || ipc.isMock) return;
    const ticket = ++epoch.current, id = attachment.id, sha = attachment.sha256;
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && sessions.currentProjectId === project &&
        sessions.currentSessionId === session && attachments.projectId === project && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === sha;
    };
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await typedIPC.readAttachmentDocument({ project_id: project, session_id: session,
        expected_service_id: service, attachment_id: id, expected_sha256: sha, ...cursor });
      if (!current()) return;
      if (response.project_id !== project || response.session_id !== session || response.service_id !== service ||
          response.attachment_id !== id || response.sha256 !== sha || response.kind !== attachment.recognized_type ||
          response.start !== cursor.offset || (response.unit !== cursor.unit && response.unit_total !== 0))
        throw new Error("返回的附件身份或位置不匹配，请重新选择。");
      setResult(response);
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const search = async (cursor: AttachmentDocumentCursor & { skipBefore?: boolean }, reset: boolean) => {
    const queryLength = Array.from(query).length;
    if (!project || !session || !service || busy || ipc.isMock || !queryLength || queryLength > 128) return;
    const ticket = ++epoch.current, id = attachment.id, sha = attachment.sha256;
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && sessions.currentProjectId === project &&
        sessions.currentSessionId === session && attachments.projectId === project && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === sha;
    };
    setBusy(true); setError("");
    if (reset) { setMatches([]); setSearchCursor(null); setSearched(false); }
    const found: typeof matches = [];
    let next: (AttachmentDocumentCursor & { skipBefore?: boolean }) | null = cursor;
    try {
      for (let count = 0; count < 2 && next; count++) {
        const scan: AttachmentDocumentCursor & { skipBefore?: boolean } = next;
        const start = Math.max(0, scan.offset - queryLength + 1);
        const response = await typedIPC.readAttachmentDocument({ project_id: project, session_id: session,
          expected_service_id: service, attachment_id: id, expected_sha256: sha, unit: scan.unit, offset: start });
        if (!current()) return;
        if (response.project_id !== project || response.session_id !== session || response.service_id !== service ||
            response.attachment_id !== id || response.sha256 !== sha || response.kind !== attachment.recognized_type ||
            response.start !== start || (response.unit !== scan.unit && response.unit_total !== 0))
          throw new Error("返回的附件身份或位置不匹配，请重新选择。");
        const characters = Array.from(response.text);
        let position = 0;
        while (true) {
          const index = response.text.indexOf(query, position);
          if (index < 0) break;
          const characterIndex = Array.from(response.text.slice(0, index)).length;
          const matchStart = response.start + characterIndex, matchEnd = matchStart + queryLength;
          if (scan.skipBefore ? matchStart >= scan.offset : matchEnd > scan.offset) {
            found.push({ unit: response.unit, location: response.location, start: matchStart, end: matchEnd,
              snippet: characters.slice(Math.max(0, characterIndex - 80), characterIndex + queryLength + 80).join("") });
            if (found.length === 20) { next = { unit: response.unit, offset: matchStart + 1, skipBefore: true }; break; }
          }
          position = index + (response.text.codePointAt(index)! > 0xffff ? 2 : 1);
        }
        if (found.length === 20) break;
        next = response.next_cursor;
      }
      setMatches(previous => reset ? found : [...previous, ...found]);
      setSearchCursor(next); setSearched(true);
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  const number = Number(unit);
  return <div className="space-y-2 rounded border border-line p-3" aria-label="文档分段读取">
    <h4 className="font-medium">手动读取文档片段</h4>
    <p className="text-xs text-ink-1">{attachment.recognized_type === "pdf"
      ? "按 PDF 物理页读取已有文字，不还原版式；扫描图像未尝试 OCR。"
      : "按正文和表格段落读取，不含页眉页脚、脚注、修订、文本框及图像文字；未尝试 OCR。"}空文本不代表原件无内容，内容未经核验。每次最多 4000 字符，不自动加入对话。</p>
    <div className="flex flex-wrap gap-2"><Input aria-label={attachment.recognized_type === "pdf" ? "物理页序号" : "正文段落序号"}
      type="number" min={1} max={attachment.recognized_type === "pdf" ? 200 : 10000} value={unit} onChange={(event) => setUnit(event.target.value)} />
      <Button type="button" variant="secondary" disabled={!project || !session || !service || busy || ipc.isMock || !Number.isInteger(number) || number < 1 || number > (attachment.recognized_type === "pdf" ? 200 : 10000)} onClick={() => void read({ unit: number, offset: 0 })}>读取指定位置</Button>
      {result?.next_cursor && <Button type="button" variant="secondary" disabled={busy} onClick={() => void read(result.next_cursor!)}>读取下一片段</Button>}</div>
    <div className="space-y-2 border-t border-line pt-3" aria-label="文档内容搜索">
      <h4 className="font-medium">搜索文档文字</h4>
      <p className="text-xs text-ink-1">区分大小写的原文匹配；每次搜索最多两个片段，继续搜索才能覆盖后续位置。匹配不跨页或段落；范围和 OCR 限制同上。</p>
      <Input aria-label="搜索文字" value={query} maxLength={128} onChange={event => { setQuery(event.target.value); setMatches([]); setSearchCursor(null); setSearched(false); }} disabled={busy} />
      <Button type="button" variant="secondary" disabled={busy || !query || !project || !session || !service || ipc.isMock} onClick={() => void search({ unit: 1, offset: 0 }, true)}>从头搜索</Button>
      {searchCursor && <Button type="button" variant="secondary" disabled={busy} onClick={() => void search(searchCursor, false)}>继续搜索</Button>}
      {searched && <p role="status">{searchCursor ? "搜索尚未完成，请继续搜索。" : "已搜索到可读取范围末尾；不代表覆盖原件全部内容。"} 已找到 {matches.length} 处。</p>}
      {matches.map((match, index) => <div key={`${match.unit}:${match.start}:${index}`} className="rounded border border-line p-2">
        <p className="text-xs">{match.location} · 字符 {match.start}–{match.end}</p>
        <p className="whitespace-pre-wrap break-words">{match.snippet}</p>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => void read({ unit: match.unit, offset: match.start })}>读取此位置</Button>
      </div>)}
    </div>
    {ipc.isMock && <p role="status">演示模式不支持文档分段读取。</p>}
    {busy && <p role="status">正在读取…</p>}
    {error && <p role="alert">读取失败：{error}</p>}
    {result && <div><p className="text-xs text-ink-1">来源：{attachment.original_name} · {attachment.id} · SHA-256 {attachment.sha256} · 位置 {result.location} · 单元 {result.unit}/{result.unit_total} · 字符 {result.start}–{result.end} · {result.status === "empty" ? "未提取到文字" : "已提取文字"} · OCR 未尝试</p>
      {result.truncated && <p role="status">此位置的文字尚未读完，请读取下一片段。</p>}
      <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3">{result.text || "（此范围未提取到文字）"}</pre>
      {onInsertReport && <Button type="button" variant="secondary" disabled={!result.text.trim() || busy} onClick={() => onInsertReport(result.text, `${result.location}，字符 ${result.start}–${result.end}${result.truncated ? "，此位置未读完" : ""}`)}>将此片段追加到报告正文</Button>}
      {!result.next_cursor && <p>已到可读取范围末尾；不代表覆盖原件全部内容。</p>}</div>}
  </div>;
}

