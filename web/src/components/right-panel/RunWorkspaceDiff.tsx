import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunWorkspaceDiffResult } from "../../types/ipc";
import { Button } from "../../ui";

export function RunWorkspaceDiff({ run }: { run: AgentRun }): JSX.Element {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const projectId = useSessionStore((state) => state.currentProjectId) ?? "inbox";
  const workspace = typeof run.metadata?.workspace_snapshot === "string" ? run.metadata.workspace_snapshot : null;
  const runProject = typeof run.metadata?.project_id === "string" ? run.metadata.project_id : null;
  const [result, setResult] = useState<RunWorkspaceDiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const version = useRef(0);
  const available = !!workspace && !!runProject && run.session_id === sessionId && runProject === projectId;

  useEffect(() => {
    const revision = version;
    ++revision.current; setResult(null); setError(null); setLoading(false);
    return () => { ++revision.current; };
  }, [run.id, sessionId, projectId]);
  useEffect(() => ipc.onSideCar(() => {
    ++version.current; setResult(null); setError(null); setLoading(false);
  }), []);

  const load = async () => {
    if (!available || !sessionId) return;
    const request = ++version.current, host = ipc.agentBaseUrl;
    setLoading(true); setError(null); setResult(null);
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    try {
      const response = await typedIPC.gitRunWorkspaceDiff(run.id, sessionId);
      if (!current()) return;
      if (response.run_id !== run.id || response.session_id !== sessionId || response.project_id !== runProject
          || response.workspace !== workspace || response.scope !== "current_workspace") throw new Error("任务工作区关联不匹配，请刷新后重试");
      setResult(response);
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (current()) setLoading(false); }
  };

  return <div className="mt-2 text-xs" data-testid={`run-workspace-diff-${run.id}`}>
    <Button variant="secondary" size="sm" disabled={!available || loading} loading={loading} onClick={() => void load()}>查看工作区当前差异</Button>
    {!available && <p className="text-ink-2">任务的工作区或项目绑定不可确认，不能打开此视图。</p>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {result && <div className="mt-2 space-y-2">
      <p className="break-all">当前工作区：{result.workspace}</p>
      <p>这是当前共享工作区的状态，可能包含用户或其他任务的修改，不能归因于此任务。未跟踪文件只列名称，不显示内容；已提交变更不在此视图中。</p>
      <details open><summary>未暂存的已跟踪文件差异</summary><pre aria-label="未暂存差异" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{result.diff || "无未暂存差异"}</pre></details>
      <details><summary>已暂存的差异</summary><pre aria-label="已暂存差异" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{result.staged_diff || "无已暂存差异"}</pre></details>
      <details><summary>未跟踪文件（{result.untracked.length}）</summary><pre aria-label="未跟踪文件" className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{result.untracked.join("\n") || "无未跟踪文件"}</pre></details>
    </div>}
  </div>;
}
