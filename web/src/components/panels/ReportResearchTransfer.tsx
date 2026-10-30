import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { PageExcerptRange, ResearchSource } from "../../types/ipc";
import { Button } from "../../ui";

export type ResearchDraftItem = { source: ResearchSource; number: number; excerpt: string; truncated: boolean };
/** Resolve every ID before changing the report; never use search snippets as sources. */
export function ReportResearchTransfer({ ids, project, session, service, host, onApply, onClear, onDraft, selected, manualCount, ranges, draftVersion }: {
  ids: string[]; project: string | null; session: string | null; service: string | null; host: string;
  onApply: (sources: ResearchSource[]) => string | null; onClear: () => void; onDraft: (items: ResearchDraftItem[]) => string | null;
  selected: ResearchSource[]; manualCount: number; ranges: PageExcerptRange[]; draftVersion: string;
}): JSX.Element {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [candidate, setCandidate] = useState<{ items: ResearchDraftItem[]; revision: number } | null>(null);
  const epoch = useRef(0), latest = useRef({ onApply, onClear, onDraft });
  const draft = useRef({ value: draftVersion, revision: 0 });
  if (draft.current.value !== draftVersion) draft.current = { value: draftVersion, revision: draft.current.revision + 1 };
  latest.current = { onApply, onClear, onDraft };
  useEffect(() => { epoch.current++; setBusy(false); setError(""); setCandidate(null); return () => { epoch.current++; }; }, [ids, project, session, service, host]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setBusy(false); setCandidate(null); setError("连接变化，请重新交接来源。"); }), []);
  const add = async () => {
    if (!project || !session || !service || busy) return;
    const ticket = ++epoch.current;
    const current = () => {
      const active = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && attachments.projectId === project && attachments.host === host && attachments.serviceId === service;
    };
    setBusy(true); setError("");
    try {
      if (!ids.length || ids.length > 10 || new Set(ids).size !== ids.length) throw new Error("待交接来源无效，请重新选择");
      const sources: ResearchSource[] = [];
      for (const id of ids) {
        const response = await typedIPC.getResearchSource({ project_id: project, session_id: session, expected_service_id: service, page_source_id: id });
        if (!current()) return;
        if (response.project_id !== project || response.session_id !== session || response.service_id !== service || response.source.page_source_id !== id)
          throw new Error("来源归属已变化，请重新选择");
        sources.push(response.source);
      }
      if (!current()) return;
      const problem = latest.current.onApply(sources);
      if (problem) throw new Error(problem);
      latest.current.onClear();
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setBusy(false); }
  };
  const createDraft = async (confirm: boolean) => {
    if (!project || !session || !service || busy || (confirm && !candidate)) return;
    const ticket = ++epoch.current, revision = confirm ? candidate!.revision : draft.current.revision;
    const current = () => {
      const active = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && draft.current.revision === revision && ipc.agentBaseUrl === host && active.currentProjectId === project &&
        active.currentSessionId === session && attachments.projectId === project && attachments.host === host && attachments.serviceId === service;
    };
    setBusy(true); setError("");
    try {
      if (!ids.length || ids.length > 10 || new Set(ids).size !== ids.length) throw new Error("待交接来源无效，请重新选择");
      if (!current()) throw new Error("报告草稿或归属已变化，请重新准备");
      const ordered = [...selected.map((source) => source.page_source_id)];
      for (const id of ids) if (!ordered.includes(id)) ordered.push(id);
      if (manualCount + ordered.length > 30) throw new Error("加入后来源超过 30 条，请先移除部分来源");
      const items: ResearchDraftItem[] = [];
      for (const id of ids) {
        const response = await typedIPC.getResearchSource({ project_id: project, session_id: session, expected_service_id: service, page_source_id: id });
        if (!current()) throw new Error("报告草稿或归属已变化，请重新准备");
        if (response.project_id !== project || response.session_id !== session || response.service_id !== service || response.source.page_source_id !== id)
          throw new Error("来源归属已变化，请重新选择");
        const source = response.source, text = Array.from(source.excerpt ?? ""), range = ranges.find((item) => item.page_source_id === id);
        if (range && (range.start < 0 || range.end <= range.start || range.end > text.length || range.end - range.start > 1000)) throw new Error("来源选段已变化，请重新选择");
        const start = range?.start ?? 0, end = range?.end ?? Math.min(600, text.length);
        items.push({ source, number: manualCount + ordered.indexOf(id) + 1,
          excerpt: text.slice(start, end).join(""), truncated: !!source.excerpt_truncated || !!source.truncated || start > 0 || end < text.length });
      }
      if (!current()) throw new Error("报告草稿或归属已变化，请重新准备");
      if (!confirm) { setCandidate({ items, revision }); return; }
      if (JSON.stringify(items) !== JSON.stringify(candidate!.items)) throw new Error("来源内容已变化，请重新准备");
      const problem = latest.current.onDraft(items);
      if (problem) throw new Error(problem);
      setCandidate(null); latest.current.onClear();
    } catch (reason) { if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-3">
    <p>待交接 {ids.length} 条研究来源：将按所选顺序重新读取，加入当前报告来源列表；重复来源跳过。页面、正文引用和事实仍需核对。</p>
    <p className="break-all text-xs">{ids.join(" · ")}</p>
    <div className="flex gap-2"><Button type="button" variant="secondary" disabled={busy || !project || !session || !service} onClick={() => void add()}>核对并加入报告</Button>
      <Button type="button" variant="secondary" disabled={busy || !project || !session || !service} onClick={() => void createDraft(false)}>准备多来源报告初稿</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={onClear}>放弃交接</Button></div>
    {candidate && <div className="space-y-2 rounded border border-line p-2"><p>初稿将追加到普通正文或已选章节，并加入所需来源；不会生成文件或自动发送。请检查来源和摘录，确认后可继续编辑。</p>
      {candidate.items.map((item) => <div key={item.source.page_source_id} className="break-all"><p>[{item.number}] {item.source.title || "（页面未提供标题）"} · {item.source.url} · {item.source.accessed_at}</p>
        <p className="whitespace-pre-wrap">{item.excerpt || "（保存的摘录为空）"}{item.truncated ? "（选段或页面已截断）" : ""}</p></div>)}
      <Button type="button" variant="secondary" disabled={busy} onClick={() => void createDraft(true)}>确认追加初稿</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setCandidate(null)}>取消初稿预览</Button></div>}
    {busy && <p role="status">正在重新读取来源…</p>}{error && <p role="alert">未加入：{error}</p>}
  </div>;
}
