import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { ResearchSource } from "../../types/ipc";
import { Button } from "../../ui";

/** Resolve every ID before changing the report; never use search snippets as sources. */
export function ReportResearchTransfer({ ids, project, session, service, host, onApply, onClear }: {
  ids: string[]; project: string | null; session: string | null; service: string | null; host: string;
  onApply: (sources: ResearchSource[]) => string | null; onClear: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), latest = useRef({ onApply, onClear });
  latest.current = { onApply, onClear };
  useEffect(() => { epoch.current++; setBusy(false); setError(""); return () => { epoch.current++; }; }, [ids, project, session, service, host]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setBusy(false); setError("连接变化，请重新交接来源。"); }), []);
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
  return <div className="space-y-2 rounded border border-line p-3">
    <p>待交接 {ids.length} 条研究来源：将按所选顺序重新读取，加入当前报告来源列表；重复来源跳过。页面、正文引用和事实仍需核对。</p>
    <p className="break-all text-xs">{ids.join(" · ")}</p>
    <div className="flex gap-2"><Button type="button" variant="secondary" disabled={busy || !project || !session || !service} onClick={() => void add()}>核对并加入报告</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={onClear}>放弃交接</Button></div>
    {busy && <p role="status">正在重新读取来源…</p>}{error && <p role="alert">未加入：{error}</p>}
  </div>;
}
