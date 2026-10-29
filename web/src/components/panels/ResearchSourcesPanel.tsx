import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { ResearchSource, ResearchSourceScope } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { ManualResearch } from "./ManualResearch";

function safeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function ResearchSourcesPanel({ onText, onClose, embedded = false }: { onText: () => void; onClose: () => void; embedded?: boolean }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId);
  const session = useSessionStore((state) => state.currentSessionId);
  const [host, setHost] = useState(ipc.agentBaseUrl);
  const [epoch, setEpoch] = useState(0);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<ResearchSource[]>([]);
  const [service, setService] = useState<string | null>(null);
  const [scope, setScope] = useState<{ project: string; session: string; host: string } | null>(null);
  const [selected, setSelected] = useState<ResearchSource | null>(null);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const version = useRef(0);
  const visible = scope?.project === project && scope?.session === session && scope?.host === ipc.agentBaseUrl;
  const current = (revision: number) => version.current === revision && ipc.agentBaseUrl === host
    && useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const checkScope = (response: ResearchSourceScope) => response.session_id === session
    && response.project_id === project && !!response.service_id;

  useEffect(() => ipc.onSideCar(() => {
    ++version.current; setRows([]); setSelected(null); setBatchIds([]); setService(null); setScope(null); setError(null); setAdding(false); setAdded(false);
    setHost(ipc.agentBaseUrl);
    setEpoch((value) => value + 1);
  }), []);
  useEffect(() => { ++version.current; setRows([]); setSelected(null); setBatchIds([]); setService(null); setScope(null); setError(null);
    setQuery(""); setApplied(""); setOffset(0); setNotice(null); setAdding(false); setAdded(false); }, [project, session, host]);
  useEffect(() => {
    const token = version;
    const revision = ++token.current;
    setRows([]); setSelected(null); setBatchIds([]); setService(null); setScope(null); setError(null);
    if (!project || !session) { setLoading(false); return; }
    setLoading(true);
    void typedIPC.listResearchSources({ session_id: session, project_id: project, query: applied, offset }).then((result) => {
      if (!current(revision)) return;
      if (!checkScope(result) || result.sources.length > 20) throw new Error("来源列表关联不匹配");
      setRows(result.sources); setService(result.service_id); setScope({ project, session, host });
    }).catch((reason: unknown) => { if (current(revision)) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (current(revision)) setLoading(false); });
    return () => { ++token.current; };
  // Session, project and service host define the authority of each request.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, session, host, epoch, applied, offset]);

  const select = async (id: string) => {
    if (!project || !session || !service) return;
    const revision = ++version.current, expected = service;
    setSelected(null); setError(null); setNotice(null); setAdded(false); setAdding(false); setLoading(true);
    try {
      const result = await typedIPC.getResearchSource({ session_id: session, project_id: project,
        expected_service_id: expected, page_source_id: id });
      if (!current(revision)) return;
      if (!checkScope(result) || result.service_id !== expected || result.source.page_source_id !== id) throw new Error("来源详情关联不匹配");
      setSelected(result.source);
    } catch (reason) { if (current(revision)) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current(revision)) setLoading(false); }
  };

  const addToDraft = async (ids: string[]) => {
    if (!visible || !project || !session || !service || !ids.length || adding) return;
    const revision = ++version.current, expected = service;
    setAdding(true); setNotice(null); setAdded(false);
    try {
      const sources: ResearchSource[] = [];
      for (const id of ids) {
        const result = await typedIPC.getResearchSource({ session_id: session, project_id: project,
          expected_service_id: expected, page_source_id: id });
        if (!current(revision)) return;
        if (!checkScope(result) || result.service_id !== expected || result.source.page_source_id !== id)
          throw new Error("来源或服务关联已变化，请重新选择");
        sources.push(result.source);
      }
      const text = sources.map((source) => {
        const excerpt = Array.from(source.excerpt ?? ""), shortened = excerpt.length > 600;
        return ["研究来源（页面内容未核验，请核对后使用）：", `标题：${source.title || "（页面未提供标题）"}${source.title_truncated ? "（已截断）" : ""}`,
          `网址：${source.url}`, `实际访问时间：${source.accessed_at}`, `来源 ID：${source.page_source_id}`,
          `所属运行：${source.run_id}`, `摘录${shortened || source.excerpt_truncated || source.truncated ? "（已截断，不代表全文）" : ""}：`,
          excerpt.slice(0, 600).join("") || "（无提取文本）"].join("\n");
      }).join("\n\n");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "当前聊天输入区未打开；请先切换到聊天，再重新选择来源。");
      setAdded(detail.result === "已添加到当前会话草稿，尚未发送。");
    } catch (reason) { if (current(revision)) setNotice(`未添加来源：${reason instanceof Error ? reason.message : String(reason)}`); }
    finally { if (current(revision)) setAdding(false); }
  };

  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="项目研究来源">
    <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">项目研究来源</h2>
      {!embedded && <div className="flex gap-2"><Button variant="secondary" onClick={onText}>文本成果</Button><Button variant="ghost" onClick={onClose}>关闭</Button></div>}</div>
    <p className="text-sm text-ink-1">仅展示本项目在此服务读取并记录的网页。页面内容和报告事实未自动核验；来源独立于所属任务和会话保存；删除项目会删除来源记录。所属任务 ID 可能已不存在。</p>
    <ManualResearch project={project} session={session} service={visible ? service : null} />
    {!project || !session ? <p role="status">请先选择当前项目的会话。</p> : <>
      <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); if (adding) return; setOffset(0); setApplied(query); }}>
        <Input aria-label="搜索研究来源" maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="标题、网址或摘录" />
        <Button type="submit" disabled={loading || adding}>搜索</Button>
        <Button type="button" variant="secondary" disabled={loading || adding} onClick={() => setEpoch((value) => value + 1)}>刷新</Button>
      </form>
      {loading && <p role="status">正在读取来源…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && visible && !rows.length && <p>当前范围没有匹配的来源。</p>}
      <ul className="space-y-1">{(visible ? rows : []).map((row) => <li key={row.page_source_id} className="flex items-center gap-2">
        <input type="checkbox" aria-label={`选择来源 ${row.title || row.url}`} checked={batchIds.includes(row.page_source_id)}
          disabled={adding || loading || (batchIds.length >= 10 && !batchIds.includes(row.page_source_id))}
          onChange={(event) => { const checked = event.target.checked; setBatchIds((ids) => checked ? [...ids, row.page_source_id] : ids.filter((id) => id !== row.page_source_id)); }} />
        <button type="button" className="w-full rounded border border-line p-2 text-left" disabled={adding} onClick={() => void select(row.page_source_id)}>
          {row.title || "（页面未提供标题）"} · {row.accessed_at}
          {row.excerpt_match && <span className="mt-1 block whitespace-pre-wrap break-words text-xs text-ink-2">
            保存摘录中命中（字符 {row.excerpt_match.match_start}–{row.excerpt_match.match_end}，从 0 起）：{row.excerpt_match.text}
          </span>}
        </button></li>)}</ul>
      <div className="flex flex-wrap items-center gap-2"><Button type="button" variant="secondary" loading={adding}
        disabled={loading || adding || !visible || batchIds.length === 0} onClick={() => void addToDraft(batchIds)}>将所选 {batchIds.length} 条加入草稿</Button>
        <span className="text-xs text-ink-2">每页最多选 10 条；逐条重新核对后一次追加，不发送。翻页或搜索会清空选择。</span></div>
      {notice && !selected && <p role="status">{notice}</p>}
      {added && !selected && <Button type="button" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
      <div className="flex gap-2"><Button variant="secondary" disabled={loading || adding || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</Button>
        <Button variant="secondary" disabled={loading || adding || !visible || rows.length < 20 || offset >= 10000} onClick={() => setOffset(offset + 20)}>下一页</Button></div>
      {visible && selected && <div className="space-y-2 rounded border border-line p-3">
        <h3 className="font-medium">{selected.title || "（页面未提供标题）"}</h3>
        {selected.title_truncated && <p>页面标题已截断。</p>}
        <p className="break-all">网址：{safeUrl(selected.url) ? <a href={safeUrl(selected.url)!} target="_blank" rel="noopener noreferrer" className="underline">{selected.url}</a> : selected.url}</p>
        <p>实际访问时间：{selected.accessed_at}</p><p className="break-all">所属任务：{selected.run_id}</p>
        <p className="break-all">来源 ID：{selected.page_source_id}</p>
        <div className="flex flex-wrap gap-2"><Button type="button" variant="primary" loading={adding} disabled={adding || loading} onClick={() => void addToDraft([selected.page_source_id])}>加入当前会话草稿</Button>
        <Button variant="secondary" onClick={() => {
          if (!navigator.clipboard) { setNotice("复制不可用，请手动选择 ID。"); return; }
          void navigator.clipboard.writeText(selected.page_source_id).then(() => setNotice("来源 ID 已复制。"), () => setNotice("复制失败，请手动选择 ID。"));
        }}>复制来源 ID</Button>
        {added && <Button type="button" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}</div>
        <p className="text-xs text-ink-2">添加前重新核对来源归属；只追加到草稿，不会发送。摘录最多 600 个字符，页面内容未自动核验。</p>
        {notice && <p role="status">{notice}</p>}
        <p>保存的正文摘录（未经核验）：</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-1 p-2">{selected.excerpt || "（无提取文本）"}</pre>
        {(selected.excerpt_truncated || selected.truncated) && <p>网页内容或摘录已截断，不代表全文。</p>}
      </div>}
    </>}
  </section>;
}
