import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { PageExcerptRange, ResearchSource } from "../../types/ipc";
import { Button, Input } from "../../ui";

export function ReportResearchPicker({ project, session, service, selected, onChange, manualCount, onInsertExcerpt, ranges, onRangeChange }:
  { project: string | null; session: string | null; service: string | null; selected: ResearchSource[];
    onChange: (items: ResearchSource[]) => void; manualCount: number;
    onInsertExcerpt?: (source: ResearchSource, number: number, range?: PageExcerptRange) => void;
    ranges: PageExcerptRange[]; onRangeChange: (ranges: PageExcerptRange[]) => void }): JSX.Element {
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<ResearchSource | null>(null);
  const [passage, setPassage] = useState<PageExcerptRange | null>(null);
  const [rows, setRows] = useState<ResearchSource[]>([]);
  const [offset, setOffset] = useState(0);
  const [applied, setApplied] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const epoch = useRef(0);
  const host = ipc.agentBaseUrl;
  const selection = useRef({ service, manualCount, ids: "", rangeKey: "", onInsertExcerpt });
  selection.current = { service, manualCount, ids: selected.map((item) => item.page_source_id).join("\n"), rangeKey: JSON.stringify(ranges), onInsertExcerpt };
  useEffect(() => {
    const off = ipc.onSideCar(() => { epoch.current++; setRows([]); setDetail(null); setPassage(null); setBusy(false); setError("连接变化，请重新搜索来源。"); });
    return () => { epoch.current++; off(); };
  }, []);
  useEffect(() => { epoch.current++; setRows([]); setQuery(""); setApplied(""); setOffset(0); setError(""); setBusy(false); setDetail(null); setPassage(null); }, [project, session, host, service]);
  const search = async (term: string, start: number) => {
    if (!project || !session || !service) return;
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
    setBusy(true); setError(""); setRows([]);
    try {
      const result = await typedIPC.listResearchSources({ project_id: project, session_id: session, query: term, offset: start });
      if (!current()) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.sources.length > 20)
        throw new Error("来源与当前项目或服务不匹配，请刷新");
      setRows(result.sources); setApplied(term); setOffset(start);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setBusy(false); }
  };
  const insertExcerpt = async (row: ResearchSource, index: number) => {
    if (!project || !session || !service || !onInsertExcerpt || busy) return;
    const ticket = ++epoch.current, expected = selection.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session &&
      selection.current.service === expected.service && selection.current.manualCount === expected.manualCount &&
      selection.current.ids === expected.ids && selection.current.rangeKey === expected.rangeKey && selection.current.onInsertExcerpt === expected.onInsertExcerpt;
    setBusy(true); setError("");
    try {
      const result = await typedIPC.getResearchSource({ project_id: project, session_id: session,
        expected_service_id: service, page_source_id: row.page_source_id });
      if (!current()) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service ||
          result.source.page_source_id !== row.page_source_id) throw new Error("来源归属已变化，请重新选择");
      if (!result.source.excerpt?.trim()) throw new Error("此来源没有可用摘录");
      const range = ranges.find((item) => item.page_source_id === row.page_source_id);
      if (range && range.end > Array.from(result.source.excerpt).length) throw new Error("保存的摘录已变化，请重新选择选段");
      onInsertExcerpt(result.source, manualCount + index + 1, range);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const openDetail = async (row: ResearchSource) => {
    if (!project || !session || !service || busy) return;
    const ticket = ++epoch.current;
    setBusy(true); setError(""); setDetail(null); setPassage(null);
    try {
      const result = await typedIPC.getResearchSource({ project_id: project, session_id: session,
        expected_service_id: service, page_source_id: row.page_source_id });
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host ||
          useSessionStore.getState().currentProjectId !== project || useSessionStore.getState().currentSessionId !== session) return;
      if (result.project_id !== project || result.session_id !== session || result.service_id !== service ||
          result.source.page_source_id !== row.page_source_id) throw new Error("来源归属已变化，请重新选择");
      setDetail(result.source);
    } catch (reason) { if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p>项目研究来源：从此服务实际读取的记录中选择；手工来源在前，所选来源按下方顺序编号。页面与报告事实未自动核验，来源独立于任务和会话保留；删除项目后无法使用。</p>
    {!session || !project ? <p>请先选择当前项目的会话。</p> : <>
      <div className="flex gap-2"><Input aria-label="搜索报告研究来源" maxLength={200} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="标题、网址或摘录" />
        <Button type="button" disabled={busy || !service} onClick={() => void search(query, 0)}>搜索来源</Button></div>
      {busy && <p role="status">正在读取来源…</p>}{error && <p role="alert">{error}</p>}
      {rows.map((row) => <div key={row.page_source_id} className="rounded border border-line p-2">
        <p>{row.title || "（页面未提供标题）"} · {row.accessed_at}</p><p className="break-all">{row.url}</p>
        <p className="whitespace-pre-wrap break-words">{row.excerpt || "（无摘录）"}</p>
        {row.excerpt_match && <p className="whitespace-pre-wrap break-words text-xs text-ink-2">保存摘录中命中（字符 {row.excerpt_match.match_start}–{row.excerpt_match.match_end}，从 0 起）：{row.excerpt_match.text}</p>}
        {(row.title_truncated || row.excerpt_truncated || row.truncated) && <p>标题或摘录已截断。</p>}
        <Button type="button" variant="secondary" disabled={manualCount + selected.length >= 30 || selected.some((item) => item.page_source_id === row.page_source_id)} onClick={() => onChange([...selected, row])}>添加到报告</Button>
      </div>)}
      <div className="flex gap-2"><Button type="button" variant="secondary" disabled={busy || offset === 0} onClick={() => void search(applied, Math.max(0, offset - 20))}>上一页</Button>
        <Button type="button" variant="secondary" disabled={busy || rows.length < 20 || offset >= 10000} onClick={() => void search(applied, offset + 20)}>下一页</Button></div>
    </>}
    {selected.map((row, i) => <div key={row.page_source_id} className="rounded border border-line p-2">
      <p>[{manualCount + i + 1}] {row.title || "（页面未提供标题）"} · {row.accessed_at}</p><p className="break-all">{row.url}</p>
      <p className="whitespace-pre-wrap break-words">{row.excerpt || "（无摘录）"}</p>
      {(row.title_truncated || row.excerpt_truncated || row.truncated) && <p>标题或摘录已截断。</p>}
      <p>报告参考摘录：{ranges.find((range) => range.page_source_id === row.page_source_id)
        ? `已选字符 ${ranges.find((range) => range.page_source_id === row.page_source_id)!.start}–${ranges.find((range) => range.page_source_id === row.page_source_id)!.end}` : "默认使用开头最多 1000 字"}</p>
      <Button type="button" variant="secondary" disabled={busy || !service} onClick={() => void openDetail(row)}>选择报告参考摘录</Button>
      {ranges.some((range) => range.page_source_id === row.page_source_id) && <Button type="button" variant="secondary" disabled={busy} onClick={() => onRangeChange(ranges.filter((range) => range.page_source_id !== row.page_source_id))}>恢复默认摘录</Button>}
      {detail?.page_source_id === row.page_source_id && <div>
        <label>保存的摘录（选择原文，最多 1000 个 Unicode 字符）<textarea readOnly rows={8} value={detail.excerpt ?? ""} className="w-full rounded border border-line bg-surface-1 p-2"
          onSelect={(event) => { const input = event.currentTarget; const start = Array.from(input.value.slice(0, input.selectionStart)).length;
            const end = Array.from(input.value.slice(0, input.selectionEnd)).length;
            setPassage(start < end ? { page_source_id: row.page_source_id, start, end } : null); }} /></label>
        <Button type="button" variant="secondary" disabled={!passage || passage.page_source_id !== row.page_source_id || passage.end - passage.start > 1000}
          onClick={() => { if (passage) { onRangeChange([...ranges.filter((range) => range.page_source_id !== row.page_source_id), passage]); setDetail(null); setPassage(null); } }}>使用选中原文作为参考摘录</Button>
      </div>}
      {onInsertExcerpt && <Button type="button" variant="secondary" disabled={busy || !service || !row.excerpt?.trim()} onClick={() => void insertExcerpt(row, i)}>追加摘录及编号到正文</Button>}
      <div className="flex gap-2"><Button type="button" variant="secondary" disabled={busy || i === 0} onClick={() => { const copy = [...selected]; [copy[i - 1], copy[i]] = [copy[i], copy[i - 1]]; onChange(copy); }}>上移</Button>
        <Button type="button" variant="secondary" disabled={busy || i === selected.length - 1} onClick={() => { const copy = [...selected]; [copy[i + 1], copy[i]] = [copy[i], copy[i + 1]]; onChange(copy); }}>下移</Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => { onRangeChange(ranges.filter((range) => range.page_source_id !== row.page_source_id)); onChange(selected.filter((_, index) => index !== i)); setDetail(null); setPassage(null); }}>移除来源</Button></div>
    </div>)}
  </div>;
}
