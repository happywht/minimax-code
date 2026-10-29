import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunWorkspaceDiffResult } from "../../types/ipc";
import { Button } from "../../ui";

/** Keep matching file patches intact so their context and headers remain visible. */
function filterDiff(diff: string, query: string): string {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return diff;
  // An unexpected format may not have reliable file boundaries. Keep it intact.
  if (diff && !diff.startsWith("diff --git ")) return diff;
  return diff.split(/(?=^diff --git )/m)
    .filter((block) => block.toLocaleLowerCase().includes(needle))
    .join("");
}

export function RunWorkspaceDiff({ run }: { run: AgentRun }): JSX.Element {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const projectId = useSessionStore((state) => state.currentProjectId) ?? "inbox";
  const workspace = typeof run.metadata?.workspace_snapshot === "string" ? run.metadata.workspace_snapshot : null;
  const runProject = typeof run.metadata?.project_id === "string" ? run.metadata.project_id : null;
  const [result, setResult] = useState<RunWorkspaceDiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null);
  const [search, setSearch] = useState("");
  const [preview, setPreview] = useState<{ path: string; text: string } | null>(null);
  const version = useRef(0);
  const available = !!workspace && !!runProject && run.session_id === sessionId && runProject === projectId;

  useEffect(() => {
    const revision = version;
    ++revision.current; setResult(null); setComparison(null); setPreview(null); setError(null); setLoading(false); setSearch("");
    return () => { ++revision.current; };
  }, [run.id, sessionId, projectId]);
  useEffect(() => ipc.onSideCar(() => {
    ++version.current; setResult(null); setComparison(null); setPreview(null); setError(null); setLoading(false);
  }), []);

  const load = async () => {
    if (!available || !sessionId) return;
    const request = ++version.current, host = ipc.agentBaseUrl;
    setLoading(true); setError(null); setResult(null); setPreview(null);
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

  const loadComparison = async () => {
    if (!available || !sessionId) return;
    const request = ++version.current, host = ipc.agentBaseUrl;
    setLoading(true); setError(null); setComparison(null);
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    try {
      const response = await typedIPC.getRunSteps(run.id);
      if (!current()) return;
      if (response.run.id !== run.id || response.run.session_id !== sessionId
          || response.run.metadata?.project_id !== runProject
          || response.run.metadata?.workspace_snapshot !== workspace) throw new Error("任务绑定不匹配");
      const step = response.steps.find((item) => item.payload?.source === "run_file_changes");
      setComparison(step?.payload ?? { available: false });
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (current()) setLoading(false); }
  };

  const previewPath = async (path: string) => {
    if (!result || !sessionId || !available) return;
    const request = ++version.current, host = ipc.agentBaseUrl;
    setLoading(true); setError(null); setPreview(null);
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    try {
      const response = await typedIPC.previewRunUntracked({ run_id: run.id, session_id: sessionId,
        project_id: result.project_id, workspace: result.workspace, workspace_identity: result.workspace_identity,
        expected_service_id: result.service_id, untracked_path: path });
      if (!current()) return;
      if (response.run_id !== run.id || response.session_id !== sessionId || response.project_id !== result.project_id
          || response.workspace !== result.workspace || response.workspace_identity !== result.workspace_identity
          || response.service_id !== result.service_id || response.path !== path) throw new Error("预览绑定不匹配，请刷新工作区");
      setPreview({ path, text: response.text });
    } catch (reason) {
      if (current()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (current()) setLoading(false); }
  };

  const reviewPath = (path: string) => {
    setSearch(path);
    if (!result) void load();
  };

  return <div className="mt-2 text-xs" data-testid={`run-workspace-diff-${run.id}`}>
    <div className="flex flex-wrap gap-1">
    <Button variant="secondary" size="sm" disabled={!available || loading} loading={loading} onClick={() => void load()}>查看工作区当前差异</Button>
    <Button variant="secondary" size="sm" disabled={!available || loading} onClick={() => void loadComparison()}>查看任务前后文件变化</Button>
    </div>
    {!available && <p className="text-ink-2">任务的工作区或项目绑定不可确认，不能打开此视图。</p>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {comparison && <div className="mt-2 space-y-1">
      {comparison.available === true ? <>
        <p>两次有界采样之间的变化，可能包括用户或其他任务的修改，不能归因于此任务。只比较 Git 可见普通文件，不含忽略文件及私有运行数据。点击路径可筛选当前工作区差异；当前状态可能已改变，删除的文件也可能没有当前差异。</p>
        {([['added', '新增'], ['modified', '修改'], ['deleted', '删除']] as const).map(([key, label]) =>
          <details key={key}><summary>{label}（{Array.isArray(comparison[key]) ? comparison[key].length : 0}）</summary>
            <ul className="max-h-40 overflow-auto">
              {Array.isArray(comparison[key]) && (comparison[key] as unknown[]).map((path, index) => typeof path === "string" &&
                <li key={`${index}-${path}`}><button type="button" disabled={loading} onClick={() => reviewPath(path)}
                  className="w-full break-all py-1 text-left text-accent underline disabled:opacity-50">{path}</button></li>)}
            </ul></details>)}
      </> : <p>未采集、尚未完成，或采样失败／超过限制；无法给出变化结论。</p>}
    </div>}
    {result && <div className="mt-2 space-y-2">
      <p className="break-all">当前工作区：{result.workspace}</p>
      <p>这是当前共享工作区的状态，可能包含用户或其他任务的修改，不能归因于此任务。未跟踪文件需手动选择才读取预览；已提交变更不在此视图中。</p>
      <label className="block">搜索文件名或差异内容
        <input value={search} onChange={(event) => setSearch(event.target.value)}
          className="mt-1 block w-full rounded border border-line bg-surface-1 px-2 py-1 text-ink-0"
          placeholder="输入关键词筛选" />
      </label>
      {search && <Button variant="secondary" size="sm" onClick={() => setSearch("")}>清除筛选</Button>}
      {search.trim() && <p className="text-ink-2">显示包含关键词的完整文件差异；未跟踪文件只按名称筛选。</p>}
      {search.trim() && ((result.diff && !result.diff.startsWith("diff --git ")) || (result.staged_diff && !result.staged_diff.startsWith("diff --git "))) &&
        <p className="text-ink-2">部分差异格式无法按文件筛选，已保留完整原文。</p>}
      <details open><summary>未暂存的已跟踪文件差异</summary><pre aria-label="未暂存差异" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{filterDiff(result.diff, search) || (result.diff ? "没有匹配的未暂存差异" : "无未暂存差异")}</pre></details>
      <details open={search.trim() ? true : undefined}><summary>已暂存的差异</summary><pre aria-label="已暂存差异" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{filterDiff(result.staged_diff, search) || (result.staged_diff ? "没有匹配的已暂存差异" : "无已暂存差异")}</pre></details>
      <details open={search.trim() ? true : undefined}><summary>未跟踪文件（{result.untracked.length}）</summary>
        <ul aria-label="未跟踪文件" className="max-h-40 overflow-auto">{result.untracked.filter((path) => path.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).map((path) =>
          <li key={path}><button type="button" disabled={loading} onClick={() => void previewPath(path)} className="w-full break-all py-1 text-left text-accent underline disabled:opacity-50">{path}</button></li>)}</ul>
        {!result.untracked.length && <p>无未跟踪文件</p>}
      </details>
      {preview && <div><p className="break-all">只读文本预览：{preview.path}（最多 64 KiB；文件可能已变化）</p><pre aria-label="未跟踪文件预览" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{preview.text || "（空文件）"}</pre></div>}
    </div>}
  </div>;
}
