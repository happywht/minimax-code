import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchArtifactContent } from "../../ipc/artifactContent";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { TextArtifact } from "../../types/ipc";
import { Button, Input } from "../../ui";

/** Reuse immutable project snapshots as editable report paragraphs. */
export function ReportArtifactReuse({ project, session, service, host, onApply, transfer, onClearTransfer }: {
  project: string | null; session: string | null; service: string | null; host: string;
  onApply: (paragraphs: string[]) => boolean; transfer?: TextArtifact | null; onClearTransfer?: () => void;
}): JSX.Element {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<TextArtifact[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [activeQuery, setActiveQuery] = useState("");
  const [candidate, setCandidate] = useState<{ row: TextArtifact; text: string } | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  useEffect(() => {
    const invalidate = () => { ++epoch.current; setRows([]); setCursor(null); setCandidate(null); setBusy(false); };
    invalidate(); setNotice("");
    const offSession = useSessionStore.subscribe((s) => {
      if (s.currentProjectId !== project || s.currentSessionId !== session) invalidate();
    });
    const offAttachment = useAttachmentStore.subscribe((s) => {
      if (s.projectId !== project || s.host !== host || s.serviceId !== service) invalidate();
    });
    const offConnection = ipc.onSideCar(invalidate);
    return () => { ++epoch.current; offSession(); offAttachment(); offConnection(); };
  }, [project, session, service, host]);
  useEffect(() => {
    ++epoch.current; setCandidate(null); setBusy(false); setNotice("");
  }, [transfer?.id, transfer?.sha256, transfer?.run_id]);
  const current = (ticket: number) => {
    const s = useSessionStore.getState(), a = useAttachmentStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && s.currentProjectId === project &&
      s.currentSessionId === session && a.projectId === project && a.host === host && a.serviceId === service;
  };
  const checkScope = async (ticket: number) => {
    if (!project || !session || !service || !current(ticket)) throw new Error("项目、会话或服务已变化，请重新选择。");
    const [identity, result] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
    if (!current(ticket) || identity.service_id !== service || result.session.id !== session || result.session.project_id !== project)
      throw new Error("项目、会话或服务已变化，请重新选择。");
  };
  const list = async (more = false) => {
    if (!project || !session || !service || busy) return;
    const ticket = ++epoch.current, nextQuery = more ? activeQuery : query, nextCursor = more ? cursor : null;
    setBusy(true); setNotice(""); setCandidate(null);
    try {
      await checkScope(ticket);
      const result = await typedIPC.listArtifacts(project, undefined, nextCursor ?? undefined, nextQuery || undefined);
      await checkScope(ticket);
      if (result.service_id !== service || result.artifacts.some((row) => row.project_id !== project || row.service_id !== service))
        throw new Error("成果归属已变化，请重新搜索。");
      setRows((old) => more ? [...old, ...result.artifacts.filter((row) => !old.some((item) => item.id === row.id))] : result.artifacts);
      setCursor(result.next_cursor ?? null); setActiveQuery(nextQuery);
      if (!more && !result.artifacts.length) setNotice("没有匹配的文本成果。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const read = async (row: TextArtifact, ticket: number) => {
    if (!project) throw new Error("请先选择项目。");
    const checkRun = async () => {
      if (!transfer || transfer.id !== row.id) return;
      if (!session || !service || !row.run_id) throw new Error("任务关联范围已变化，请重新选择。 ");
      const result = await typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: service, run_id: row.run_id });
      if (!current(ticket) || result.project_id !== project || result.service_id !== service ||
          result.detail?.id !== row.run_id || result.detail.session_id !== session)
        throw new Error("任务或会话归属已变化，请重新选择。 ");
    };
    await checkScope(ticket);
    await checkRun();
    const result = await typedIPC.getArtifact(project, row.id), fresh = result.artifact;
    if (!current(ticket) || fresh.id !== row.id || fresh.project_id !== project || fresh.service_id !== service ||
        fresh.sha256 !== row.sha256 || fresh.snapshot_id !== row.snapshot_id || fresh.version !== row.version ||
        fresh.source_path !== row.source_path || fresh.size_bytes !== row.size_bytes || fresh.mime_type !== row.mime_type || fresh.run_id !== row.run_id)
      throw new Error("成果身份已变化，请重新选择。");
    const blob = await fetchArtifactContent(row, host, new AbortController().signal);
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await blob.arrayBuffer()).replace(/\r\n/g, "\n");
    await checkScope(ticket);
    await checkRun();
    // eslint-disable-next-line no-control-regex
    if (!text.trim() || text.startsWith("\ufeff") || /[\x00-\x08\x0b-\x1f\x7f-\x9f\ufffe\uffff]/.test(text) ||
        new TextEncoder().encode(text).length > 256 * 1024 || text.split("\n").length > 100 ||
        text.split("\n").some((line) => Array.from(line).length > 4096))
      throw new Error("文本为空、包含不支持的字符，或超过 100 行、每行 4096 字或 256 KiB；草稿未更改。");
    return text;
  };
  const prepare = async (row: TextArtifact) => {
    const ticket = ++epoch.current; setBusy(true); setCandidate(null); setNotice("");
    try { const text = await read(row, ticket); if (current(ticket)) setCandidate({ row, text }); }
    catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const apply = async () => {
    if (!candidate || busy) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const text = await read(candidate.row, ticket);
      if (!current(ticket)) return;
      if (text !== candidate.text) throw new Error("成果内容已变化，请重新准备。");
      const paragraphs = text.split("\n").filter((line) => line.trim());
      if (onApply(paragraphs)) { if (transfer?.id === candidate.row.id) onClearTransfer?.(); setCandidate(null); setNotice("已追加可编辑段落；请核对内容和引用。尚未生成文件。"); }
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    {transfer && <div className="space-y-1 rounded border border-line p-2"><p className="break-all">任务关联成果：{transfer.source_path} · v{transfer.version} · 运行 {transfer.run_id}</p>
      <p>准备时重新读取完整快照；请核对内容后再追加到报告，原文件不变。</p>
      <Button type="button" size="sm" variant="secondary" disabled={busy || !project || !session || !service || transfer.project_id !== project || transfer.service_id !== service || !transfer.run_id} onClick={() => void prepare(transfer)}>准备此任务成果</Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { setCandidate(null); onClearTransfer?.(); }}>放弃待复用成果</Button></div>}
    <p>复用已登记文本成果的完整快照：每行追加为报告段落，空行省略；Markdown 和 CSV 均作为纯文本。原文件不变，内容需核对。</p>
    <div className="flex gap-2"><Input aria-label="搜索可复用文本成果" maxLength={240} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按路径搜索" />
      <Button type="button" variant="secondary" disabled={busy || !project || !session || !service} onClick={() => void list()}>搜索成果</Button></div>
    {rows.map((row) => <div key={row.id} className="flex flex-wrap items-center gap-2"><span className="break-all">{row.source_path} · v{row.version} · {row.size_bytes} 字节</span>
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void prepare(row)}>准备正文</Button></div>)}
    {cursor && <Button type="button" variant="secondary" disabled={busy} onClick={() => void list(true)}>加载更多成果</Button>}
    {candidate && <div className="space-y-2"><p className="break-all">请核对完整快照：{candidate.row.source_path} · v{candidate.row.version} · SHA-256 {candidate.row.sha256}</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words">{candidate.text}</pre>
      <Button type="button" disabled={busy} onClick={() => void apply()}>追加到报告正文</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setCandidate(null)}>取消</Button></div>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}
