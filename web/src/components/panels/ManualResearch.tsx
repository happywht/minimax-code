import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { ResearchSource } from "../../types/ipc";
import { Button, Input } from "../../ui";

type Result = { title: string; url: string; snippet: string; title_truncated: boolean; snippet_truncated: boolean };
export function ManualResearch({ project, session, service, reportService, reportTransferPending = false, onPrepareReport }: { project: string | null; session: string | null; service: string | null; reportService?: string; reportTransferPending?: boolean; onPrepareReport?: (ids: string[]) => void }): JSX.Element {
  const [query, setQuery] = useState(""), [url, setUrl] = useState(""), [focus, setFocus] = useState("");
  const [results, setResults] = useState<Result[]>([]), [pages, setPages] = useState<{ source: ResearchSource; selected: boolean }[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [searched, setSearched] = useState(false);
  const [provider, setProvider] = useState("提供方待读取");
  const epoch = useRef(0), host = ipc.agentBaseUrl;
  useEffect(() => { epoch.current++; setQuery(""); setUrl(""); setFocus(""); setResults([]); setPages([]); setBusy(false); setError(""); setSearched(false); }, [project, session, service, host]);
  useEffect(() => {
    let active = true; setProvider("提供方待读取");
    void typedIPC.getResearchConfig().then((config) => { if (active) setProvider(config.provider === "brave" ? "Brave Search API" : "SearXNG"); }, () => { if (active) setProvider("无法读取提供方设置"); });
    return () => { active = false; };
  }, [host, service]);
  useEffect(() => { const off = ipc.onSideCar(() => { epoch.current++; setResults([]); setPages([]); setBusy(false); setError("连接变化，请重新操作。"); }); return () => { epoch.current++; off(); }; }, []);
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const search = async () => {
    if (!project || !session || !service || !query.trim()) return;
    const ticket = ++epoch.current; setBusy(true); setError(""); setResults([]); setSearched(false);
    try {
      const response = await typedIPC.searchPublicResearch({ project_id: project, session_id: session, expected_service_id: service, query: query.trim() });
      if (!current(ticket)) return;
      if (response.project_id !== project || response.session_id !== session || response.service_id !== service || response.result.results.length > 20) throw new Error("搜索响应关联不匹配");
      setResults(response.result.results); setSearched(true);
    } catch (e) { if (current(ticket)) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const read = async (target: string) => {
    if (!project || !session || !service || !target.trim() || pages.length >= 10) return;
    const ticket = ++epoch.current; setBusy(true); setError("");
    try {
      const response = await typedIPC.readPublicResearch({ project_id: project, session_id: session, expected_service_id: service, url: target.trim(), ...(focus.trim() ? { focus: focus.trim() } : {}) });
      if (!current(ticket)) return;
      if (response.project_id !== project || response.session_id !== session || response.service_id !== service || !response.result.page_source_id || !response.result.run_id) throw new Error("来源保存回执关联不匹配");
      setPages((previous) => previous.some((item) => item.source.page_source_id === response.result.page_source_id)
        ? previous : [...previous, { source: response.result, selected: true }]);
    } catch (e) { if (current(ticket)) setError(`${e instanceof Error ? e.message : String(e)}；若响应中断，请刷新来源列表确认是否已保存。`); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <details className="rounded border border-line p-3"><summary>搜索网页并保存来源</summary>
    <p>点击搜索会将查询发送给设置中的搜索服务，可能产生提供方费用。搜索摘要未经核验，也不是已读取来源；仅点击“读取并保存”才访问对应页面。仅支持公网 HTTPS；已设置的网络拒绝规则仍生效。</p>
    <p>当前配置：{provider}（不表示网络可用；设置可在 Providers → 联网研究中修改）。</p>
    <label className="block">页内定位词（可选，适用于下方所有读取操作）
      <Input aria-label="页内定位词" maxLength={200} value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="输入网页中需要引用的原文" /></label>
    <p>读取时在已下载的文本内查找首个匹配，并保存其上下文；未找到时不保存。仅查找最多 512 KiB 的网页响应，不支持未下载或动态加载的内容。留空则保存开头摘录。</p>
    <div className="flex gap-2"><Input aria-label="网页搜索内容" maxLength={500} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入搜索内容" />
      <Button type="button" disabled={busy || !project || !session || !service || !query.trim()} onClick={() => void search()}>搜索网页</Button></div>
    {searched && !results.length && <p>搜索服务未返回可用结果。</p>}
    {results.map((row, index) => <div key={`${row.url}:${index}`} className="mt-2 rounded border border-line p-2">
      <p>{row.title || "（无标题）"}</p><p className="break-all">{row.url}</p><p className="whitespace-pre-wrap break-words">{row.snippet}</p>
      {(row.title_truncated || row.snippet_truncated) && <p>搜索标题或摘要已截断。</p>}
      <Button type="button" variant="secondary" disabled={busy || pages.length >= 10 || !project || !session || !service} onClick={() => void read(row.url)}>读取并保存此页面</Button>
    </div>)}
    <div className="mt-2 flex gap-2"><Input aria-label="读取网页网址" value={url} maxLength={4096} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
      <Button type="button" disabled={busy || pages.length >= 10 || !project || !session || !service || !url.trim()} onClick={() => void read(url)}>读取并保存网址</Button></div>
    {busy && <p role="status">正在联网；切换项目或会话后将丢弃迟到的界面结果。</p>}{error && <p role="alert">{error}</p>}
    {pages.length > 0 && <div className="mt-2 space-y-2 rounded border border-line p-2"><p>本次已保存 {pages.length} 条来源。可勾选并按读取顺序交接到报告；最多暂存 10 条，移出只影响此列表，不删除已保存记录。刷新来源列表、切换范围或连接变化会清空此列表，已保存记录仍可在项目来源中查找。</p>
      {pages.map(({ source, selected }) => <div key={source.page_source_id} className="rounded border border-line p-2">
        <label><input type="checkbox" checked={selected} disabled={busy} onChange={(event) => { const checked = event.target.checked; setPages((previous) => previous.map((item) => item.source.page_source_id === source.page_source_id ? { ...item, selected: checked } : item)); }} /> {source.title || "（页面未提供标题）"}</label>
        <p className="break-all">{source.url} · {source.accessed_at}</p><p className="break-all">来源 ID：{source.page_source_id} · 记录：{source.run_id}</p>
        <details><summary>查看保存摘录</summary><p className="whitespace-pre-wrap break-words">{source.excerpt || "（无提取文本）"}</p></details>
        {(source.title_truncated || source.excerpt_truncated || source.truncated) && <p>标题或摘录已截断。</p>}
        <Button type="button" variant="ghost" disabled={busy} onClick={() => setPages((previous) => previous.filter((item) => item.source.page_source_id !== source.page_source_id))}>从本次列表移出</Button>
      </div>)}
      {pages.length >= 10 && <p role="status">本次列表已满；先移出项目再读取更多页面。</p>}
      {onPrepareReport && <Button type="button" variant="secondary" disabled={busy || !project || !session || !service || service !== reportService || reportTransferPending || !pages.some((item) => item.selected)} onClick={() => {
        if (ipc.agentBaseUrl !== host || useSessionStore.getState().currentProjectId !== project || useSessionStore.getState().currentSessionId !== session) return;
        onPrepareReport(pages.filter((item) => item.selected).map((item) => item.source.page_source_id));
      }}>交接所选来源到报告</Button>}
      {reportTransferPending && <p role="status">报告已有待处理来源，请先应用或放弃。</p>}
      <p>网页内容和报告事实未经核验；报告侧会重新读取并核对来源。</p></div>}
  </details>;
}
