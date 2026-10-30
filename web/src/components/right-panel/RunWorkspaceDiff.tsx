import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunWorkspaceDiffResult } from "../../types/ipc";
import { Button, DiffLines } from "../../ui";

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

type DiffChoice = { scope: "working" | "staged"; block: string };

/** Render complete file patches without interpreting Git's quoted path syntax. */
function FileDiffs({ diff, query, label, onSelect, disabled, selectedBlocks, onToggle, maxReached }: { diff: string; query: string; label: string; onSelect: (block: string) => void; disabled: boolean; selectedBlocks: string[]; onToggle: (block: string) => void; maxReached: boolean }): JSX.Element {
  const filtered = filterDiff(diff, query);
  if (!filtered) return <p>{diff ? `没有匹配的${label}` : `无${label}`}</p>;
  if (!filtered.startsWith("diff --git ")) {
    return <div aria-label={label} className="max-h-60 overflow-auto"><DiffLines text={filtered} /></div>;
  }
  const blocks = filtered.split(/(?=^diff --git )/m).filter(Boolean);
  return <div aria-label={label} className="max-h-96 space-y-1 overflow-auto">
    {blocks.map((block, index) => {
      const lines = block.split("\n");
      const title = lines[0].slice("diff --git ".length);
      let added = 0, removed = 0, inHunk = false;
      for (const line of lines) {
        if (line.startsWith("@@ ")) inHunk = true;
        else if (inHunk && line.startsWith("+")) added++;
        else if (inHunk && line.startsWith("-")) removed++;
      }
      return <details key={`${index}-${title}`} className="rounded border border-line">
        <summary className="cursor-pointer break-all px-2 py-1">
          {title} <span className="text-status-success">+{added}</span>{" "}
          <span className="text-status-error">-{removed}</span>
        </summary>
        <div className="overflow-auto border-t border-line"><DiffLines text={block} /></div>
        <div className="flex flex-wrap items-center gap-2 p-1">
          <Button type="button" size="sm" variant="secondary" disabled={disabled} onClick={() => onSelect(block)}>针对这份差异准备后续任务</Button>
          <label className="flex items-center gap-1"><input type="checkbox" checked={selectedBlocks.includes(block)} aria-label={`加入多文件交接：${label} ${title}`} disabled={disabled || (!selectedBlocks.includes(block) && maxReached)} onChange={() => onToggle(block)} />加入多文件交接</label>
        </div>
      </details>;
    })}
  </div>;
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
  const [selected, setSelected] = useState<DiffChoice | null>(null);
  const [handoff, setHandoff] = useState<DiffChoice[]>([]);
  const [handoffNote, setHandoffNote] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [untrackedNote, setUntrackedNote] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const version = useRef(0), appending = useRef(false);
  const available = !!workspace && !!runProject && run.session_id === sessionId && runProject === projectId;

  useEffect(() => {
    const revision = version;
    ++revision.current; setResult(null); setComparison(null); setPreview(null); setUntrackedNote(""); setError(null); setLoading(false); setSearch(""); setSelected(null); setReviewNote(""); setHandoff([]); setHandoffNote(""); setDraftMessage("");
    return () => { ++revision.current; };
  }, [run.id, sessionId, projectId]);
  useEffect(() => ipc.onSideCar(() => {
    ++version.current; setResult(null); setComparison(null); setPreview(null); setUntrackedNote(""); setSelected(null); setReviewNote(""); setHandoff([]); setHandoffNote(""); setDraftMessage(""); setError(null); setLoading(false);
  }), []);

  const load = async () => {
    if (!available || !sessionId) return;
    const request = ++version.current, host = ipc.agentBaseUrl;
    setLoading(true); setError(null); setResult(null); setPreview(null); setUntrackedNote(""); setSelected(null); setReviewNote(""); setHandoff([]); setHandoffNote(""); setDraftMessage("");
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
    setLoading(true); setError(null); setPreview(null); setUntrackedNote(""); setDraftMessage("");
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

  const appendReview = async () => {
    if (appending.current || !selected || !result || !sessionId || !available || !reviewNote.trim()) return;
    appending.current = true;
    const request = ++version.current, host = ipc.agentBaseUrl;
    const snapshot = result, choice = selected;
    setLoading(true); setDraftMessage("");
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    try {
      const fresh = await typedIPC.gitRunWorkspaceDiff(run.id, sessionId);
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (fresh.run_id !== run.id || fresh.session_id !== sessionId || fresh.project_id !== runProject ||
          fresh.workspace !== workspace || fresh.scope !== "current_workspace" ||
          fresh.service_id !== snapshot.service_id || identity.service_id !== snapshot.service_id ||
          fresh.workspace_identity !== snapshot.workspace_identity) throw new Error("工作区或服务已变化，请重新审阅。");
      const diff = choice.scope === "working" ? fresh.diff : fresh.staged_diff;
      const blocks = diff.startsWith("diff --git ") ? diff.split(/(?=^diff --git )/m).filter(Boolean) : [];
      if (!blocks.includes(choice.block)) throw new Error("所选差异已变化，请重新读取并审阅。");
      const excerpt = choice.block.slice(0, 12000);
      const text = `请处理以下审阅意见（目标和处理方式可在发送前编辑）：\n来源运行：${run.id}\n` +
        `工作区：${fresh.workspace}；${choice.scope === "working" ? "未暂存" : "已暂存"}的当前差异可能包含其他人的修改，不能归因于该运行。\n` +
        `审阅意见：\n${reviewNote.trim()}\n\n审阅时的差异：\n${excerpt}` +
        (choice.block.length > excerpt.length ? "\n（差异仅附前 12000 个字符，请重新读取完整文件。）" : "");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setDraftMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setSelected(null); setReviewNote(""); }
    } catch (reason) {
      if (current()) setDraftMessage(reason instanceof Error ? reason.message : String(reason));
    } finally { appending.current = false; if (current()) setLoading(false); }
  };

  const toggleHandoff = (scope: DiffChoice["scope"], block: string) => {
    setHandoff((previous) => {
      const exists = previous.some((item) => item.scope === scope && item.block === block);
      if (exists) return previous.filter((item) => item.scope !== scope || item.block !== block);
      return previous.length < 10 ? [...previous, { scope, block }] : previous;
    });
    setDraftMessage("");
  };

  const appendHandoff = async () => {
    if (appending.current || !handoff.length || !handoffNote.trim() || !result || !sessionId || !available) return;
    appending.current = true;
    const request = ++version.current, host = ipc.agentBaseUrl, snapshot = result, choices = handoff;
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    setLoading(true); setDraftMessage("");
    try {
      const fresh = await typedIPC.gitRunWorkspaceDiff(run.id, sessionId);
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (fresh.run_id !== run.id || fresh.session_id !== sessionId || fresh.project_id !== runProject ||
          fresh.workspace !== workspace || fresh.scope !== "current_workspace" ||
          fresh.service_id !== snapshot.service_id || identity.service_id !== snapshot.service_id ||
          fresh.workspace_identity !== snapshot.workspace_identity) throw new Error("工作区或服务已变化，请重新审阅。");
      for (const choice of choices) {
        const diff = choice.scope === "working" ? fresh.diff : fresh.staged_diff;
        const blocks = diff.startsWith("diff --git ") ? diff.split(/(?=^diff --git )/m).filter(Boolean) : [];
        if (!blocks.includes(choice.block)) throw new Error("所选差异已变化，请重新读取并审阅。");
      }
      let remaining = 20000;
      const excerpts = choices.map((choice) => {
        const length = Math.min(4000, remaining), excerpt = choice.block.slice(0, length);
        remaining -= excerpt.length;
        return `【${choice.scope === "working" ? "未暂存" : "已暂存"}】${choice.block.split("\n")[0]}\n` +
          (excerpt || "（总长度上限，未附差异内容）") +
          (choice.block.length > excerpt.length ? "\n（差异未完整加入，请重新读取原文件。）" : "");
      });
      const text = `请处理以下多文件审阅意见（发送前可编辑）：\n来源运行：${run.id}\n` +
        `工作区：${fresh.workspace}；当前差异可能包含用户或其他任务的修改，不能归因于此运行。请保留用户同期修改。\n` +
        `审阅意见：\n${handoffNote.trim()}\n\n审阅时所选差异：\n${excerpts.join("\n\n")}` +
        "\n此交接操作未执行验证；任务验证结果请查看原运行。";
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setDraftMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setHandoff([]); setHandoffNote(""); }
    } catch (reason) {
      if (current()) setDraftMessage(reason instanceof Error ? reason.message : String(reason));
    } finally { appending.current = false; if (current()) setLoading(false); }
  };

  const appendUntrackedReview = async () => {
    if (appending.current || !preview || !result || !sessionId || !available || !untrackedNote.trim()) return;
    appending.current = true;
    const request = ++version.current, host = ipc.agentBaseUrl, snapshot = result, choice = preview;
    const current = () => request === version.current && ipc.agentBaseUrl === host
      && useSessionStore.getState().currentSessionId === sessionId
      && (useSessionStore.getState().currentProjectId ?? "inbox") === projectId;
    setLoading(true); setDraftMessage("");
    try {
      const fresh = await typedIPC.previewRunUntracked({ run_id: run.id, session_id: sessionId,
        project_id: snapshot.project_id, workspace: snapshot.workspace, workspace_identity: snapshot.workspace_identity,
        expected_service_id: snapshot.service_id, untracked_path: choice.path });
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== snapshot.service_id || fresh.service_id !== snapshot.service_id ||
          fresh.run_id !== run.id || fresh.session_id !== sessionId || fresh.project_id !== runProject ||
          fresh.workspace !== workspace || fresh.workspace_identity !== snapshot.workspace_identity ||
          fresh.path !== choice.path || fresh.text !== choice.text)
        throw new Error("文件内容或工作区归属已变化，请重新预览并审阅。");
      const excerpt = fresh.text.slice(0, 12000);
      const text = `请处理以下审阅意见（发送前可编辑）：\n来源运行：${run.id}\n` +
        `工作区：${fresh.workspace}；未跟踪文件 ${fresh.path} 是当前工作区状态，可能包含用户或其他任务的修改，不能归因于此运行。\n` +
        `审阅意见：\n${untrackedNote.trim()}\n\n审阅时的文件内容：\n${excerpt || "（空文件）"}` +
        (fresh.text.length > excerpt.length ? "\n（仅附前 12000 个字符，请重新读取完整文件。）" : "") +
        "\n以上内容为只读审阅摘录；追加操作未执行验证，任务验证结果请查看原运行。请保留用户同期修改。";
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project: projectId, session: sessionId, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setDraftMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setPreview(null); setUntrackedNote(""); }
    } catch (reason) {
      if (current()) setDraftMessage(reason instanceof Error ? reason.message : String(reason));
    } finally { appending.current = false; if (current()) setLoading(false); }
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
      <p className="text-ink-2">展开文件查看完整差异；加减数字为文本行数。</p>
      <details open><summary>未暂存的已跟踪文件差异</summary><FileDiffs diff={result.diff} query={search} label="未暂存差异" disabled={loading} maxReached={handoff.length >= 10} selectedBlocks={handoff.filter((item) => item.scope === "working").map((item) => item.block)} onToggle={(block) => toggleHandoff("working", block)} onSelect={(block) => { setSelected({ scope: "working", block }); setReviewNote(""); setDraftMessage(""); }} /></details>
      <details open={search.trim() ? true : undefined}><summary>已暂存的差异</summary><FileDiffs diff={result.staged_diff} query={search} label="已暂存差异" disabled={loading} maxReached={handoff.length >= 10} selectedBlocks={handoff.filter((item) => item.scope === "staged").map((item) => item.block)} onToggle={(block) => toggleHandoff("staged", block)} onSelect={(block) => { setSelected({ scope: "staged", block }); setReviewNote(""); setDraftMessage(""); }} /></details>
      {handoff.length > 0 && <div className="space-y-2 rounded border border-line p-2" aria-label="多文件差异交接">
        <p>已选择 {handoff.length} 项（最多 10 项）；追加前逐项重新核对当前差异。</p>
        <ul className="max-h-40 overflow-auto">{handoff.map((item, index) => <li key={index} className="break-all">{item.scope === "working" ? "未暂存" : "已暂存"} · {item.block.split("\n")[0]}</li>)}</ul>
        <label className="block">交接意见或后续目标<textarea value={handoffNote} disabled={loading} maxLength={4000} onChange={(event) => setHandoffNote(event.target.value)} className="mt-1 block w-full rounded border border-line bg-surface-1 p-2 text-ink-0" rows={3} /></label>
        <Button type="button" size="sm" disabled={loading || !handoffNote.trim()} onClick={() => void appendHandoff()}>将所选差异追加到草稿</Button>{" "}
        <Button type="button" size="sm" variant="ghost" disabled={loading} onClick={() => { setHandoff([]); setHandoffNote(""); }}>清除所选差异</Button>
        <p>每项最多 4000 字符、合计最多 20000 字符；不完整的差异会标明。请先检查内容是否适合分享。不会自动发送或执行验证。</p>
      </div>}
      {selected && <div className="space-y-2 rounded border border-line p-2">
        <p className="break-all">已选择：{selected.block.split("\n")[0]}（{selected.scope === "working" ? "未暂存" : "已暂存"}）。追加前会重新读取并核对这份差异。</p>
        <label className="block">审阅意见或后续目标
          <textarea value={reviewNote} disabled={loading} maxLength={4000} onChange={(event) => setReviewNote(event.target.value)}
            className="mt-1 block w-full rounded border border-line bg-surface-1 p-2 text-ink-0" rows={3} />
        </label>
        <Button type="button" size="sm" disabled={loading || !reviewNote.trim()} onClick={() => void appendReview()}>追加到当前会话草稿</Button>{" "}
        <Button type="button" size="sm" variant="ghost" disabled={loading} onClick={() => { setSelected(null); setReviewNote(""); }}>取消</Button>
        <p>追加后可在聊天中编辑，不会自动发送；差异超过 12000 字符时只附前段。</p>
      </div>}
      {draftMessage && <p role="status" className="break-words text-ink-1">{draftMessage}</p>}
      {draftMessage === "已添加到当前会话草稿，尚未发送。" && <Button type="button" size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
      <details open={search.trim() ? true : undefined}><summary>未跟踪文件（{result.untracked.length}）</summary>
        <ul aria-label="未跟踪文件" className="max-h-40 overflow-auto">{result.untracked.filter((path) => path.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).map((path) =>
          <li key={path}><button type="button" disabled={loading} onClick={() => void previewPath(path)} className="w-full break-all py-1 text-left text-accent underline disabled:opacity-50">{path}</button></li>)}</ul>
        {!result.untracked.length && <p>无未跟踪文件</p>}
      </details>
      {preview && <div className="space-y-2 rounded border border-line p-2"><p className="break-all">只读文本预览：{preview.path}（最多 64 KiB；文件可能已变化）</p><pre aria-label="未跟踪文件预览" className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{preview.text || "（空文件）"}</pre>
        <label className="block">对此文件的审阅意见或后续目标
          <textarea value={untrackedNote} disabled={loading} maxLength={4000} onChange={(event) => setUntrackedNote(event.target.value)}
            className="mt-1 block w-full rounded border border-line bg-surface-1 p-2 text-ink-0" rows={3} />
        </label>
        <Button type="button" size="sm" disabled={loading || !untrackedNote.trim()} onClick={() => void appendUntrackedReview()}>将文件审阅意见追加到草稿</Button>{" "}
        <Button type="button" size="sm" variant="ghost" disabled={loading} onClick={() => { setPreview(null); setUntrackedNote(""); }}>取消文件审阅</Button>
        <p>追加前重新读取完整文件并核对归属；草稿只附前 12000 字符，不会自动发送或执行验证。请先检查是否适合分享。</p>
      </div>}
    </div>}
  </div>;
}
