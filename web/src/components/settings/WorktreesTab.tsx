/**
 * Worktrees tab — manage on-disk git worktrees over the `workspace.*`
 * IPC namespace (P1-4).
 *
 * `workspace.list_worktrees` returns every worktree-mode session; each
 * row shows the task title, the worktree directory and its base ref.
 * "清理" calls `workspace.delete_worktree`, which runs
 * `git worktree remove` (rmtree fallback) and demotes the session back
 * to local mode — the task and its messages are kept, only the
 * directory is released.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { GitBranch, RefreshCw, Trash2 } from "lucide-react";
import { Button, DiffLines, EmptyState, IconButton, Spinner } from "../../ui";
import { strings } from "../../ui/strings";
import { ipc, typedIPC } from "../../ipc";
import type { Session, WorktreeCommittedReview } from "../../types/ipc";
import { TabHeader } from "./fields";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { toast } from "../layout/ErrorBoundary";

export { WorktreesTab };

type MergeResult = Awaited<ReturnType<typeof typedIPC.mergeReviewedWorktree>>;
const mergeReasons: Record<string, string> = {
  already_merged: "目标分支已包含这些提交。",
  validation_failed: "项目验证命令未通过，尚未合并。",
  worktree_changed: "工作树在合并前发生变化，请重新审阅。",
  main_checkout_changed: "目标分支在合并前发生变化，请重新审阅。",
  conflicts: "合并产生冲突，合并状态已保留；请在目标目录处理冲突或中止合并。",
};

function WorktreesTab(): JSX.Element {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cleaningId, setCleaningId] = useState<string | null>(null);
  const [review, setReview] = useState<Session | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await typedIPC.listWorktreeSessions();
      setSessions(r.sessions);
    } catch (err) {
      setError(String(err));
      toast.error(strings.settings.worktrees.loadFailedToast, String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onCleanup = async (session: Session) => {
    const accepted = await requestConfirmation({
      title: strings.settings.worktrees.cleanupConfirmTitle,
      description: strings.settings.worktrees.cleanupConfirmDesc,
      confirmLabel: strings.settings.worktrees.cleanupConfirmLabel,
    });
    if (!accepted) return;
    setCleaningId(session.id);
    try {
      await typedIPC.deleteWorktree(session.id);
      // The session survives cleanup (demoted to local mode) — drop it
      // from this list because it no longer owns a worktree.
      setSessions((list) => list.filter((x) => x.id !== session.id));
      toast.success(
        strings.settings.worktrees.cleanedToast(
          session.title || strings.layout.sidebar.untitled,
        ),
      );
    } catch (err) {
      toast.error(strings.settings.worktrees.cleanupFailedToast, String(err));
    } finally {
      setCleaningId(null);
    }
  };

  return (
    <section data-testid="settings-worktrees" className="space-y-4">
      <TabHeader
        title={strings.settings.worktrees.title}
        hint={strings.settings.worktrees.hint}
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-worktrees-refresh"
            icon={<RefreshCw />}
            disabled={loading || cleaningId !== null}
            onClick={() => void refresh()}
          >
            {strings.settings.worktrees.refresh}
          </Button>
        }
      />

      {loading && sessions.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} />
        </div>
      ) : error && sessions.length === 0 ? (
        <p
          data-testid="settings-worktrees-error"
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400"
        >
          {error}
        </p>
      ) : sessions.length === 0 ? (
        <EmptyState
          title={strings.settings.worktrees.emptyTitle}
          hint={strings.settings.worktrees.emptyHint}
        />
      ) : (
        <ul className="space-y-2" data-testid="settings-worktrees-list">
          {sessions.map((s) => {
            const title = s.title || strings.layout.sidebar.untitled;
            return (
              <li
                key={s.id}
                data-testid={`settings-worktree-row-${s.id}`}
                className="flex items-center gap-3 rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong"
              >
                <GitBranch size={14} className="shrink-0 text-accent" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-xs font-medium text-ink-0">{title}</span>
                    {s.base_branch && (
                      <code className="rounded border border-line px-1 py-0.5 text-[11px] text-ink-2">
                        {s.base_branch}
                      </code>
                    )}
                  </div>
                  {s.workspace_path && (
                    <p
                      data-testid={`settings-worktree-path-${s.id}`}
                      className="mt-0.5 truncate text-[11px] text-ink-2"
                      title={s.workspace_path}
                    >
                      {s.workspace_path}
                    </p>
                  )}
                </div>
                <IconButton
                  size="sm"
                  data-testid={`settings-worktree-cleanup-${s.id}`}
                  aria-label={strings.settings.worktrees.cleanupAria(title)}
                  title={strings.settings.worktrees.cleanupAria(title)}
                  disabled={cleaningId !== null}
                  onClick={() => void onCleanup(s)}
                >
                  {cleaningId === s.id ? <Spinner size={12} /> : <Trash2 />}
                </IconButton>
                <Button size="sm" variant="secondary" disabled={cleaningId !== null || s.engine !== "native"} onClick={() => setReview(s)}>审阅未提交差异</Button>
              </li>
            );
          })}
        </ul>
      )}
      {review && <WorktreeChanges key={review.id} session={review} onClose={() => setReview(null)} />}
      <p className="text-xs text-ink-2">未提交差异审阅支持 Native 工作树会话；其他引擎请使用对应会话的审阅入口。</p>
    </section>
  );
}

function WorktreeChanges({ session, onClose }: { session: Session; onClose: () => void }): JSX.Element {
  const [value, setValue] = useState<{ working: string; staged: string; untracked: string[]; excluded: number; service: string } | null>(null);
  const [file, setFile] = useState<{ path: string; content: string; end: number } | null>(null);
  const [committed, setCommitted] = useState<WorktreeCommittedReview | null>(null);
  const [mergeResult, setMergeResult] = useState<MergeResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => { const stop = ipc.onSideCar(() => { ++epoch.current; setValue(null); setFile(null); setCommitted(null); setMergeResult(null); setBusy(false); setError("连接变化，请重新读取。"); }); return () => { ++epoch.current; stop(); }; }, []);
  const load = async () => {
    if (busy || !session.workspace_path) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setValue(null); setFile(null); setError("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      const before = await typedIPC.getSession(session.id);
      const working = await typedIPC.gitDiff({ scope: "working", session_id: session.id });
      const staged = await typedIPC.gitDiff({ scope: "staged", session_id: session.id });
      const status = await typedIPC.gitStatus({ session_id: session.id });
      const after = await typedIPC.getSession(session.id);
      const identityAfter = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      const matches = (row: Session) => row.id === session.id && row.project_id === session.project_id && row.engine === "native" &&
        row.workspace_mode === "worktree" && row.workspace_path === session.workspace_path && !row.archived;
      if (!identity.service_id || identity.service_id !== identityAfter.service_id || !matches(before.session) || !matches(after.session) ||
          working.session_id !== session.id || staged.session_id !== session.id || working.workspace !== session.workspace_path ||
          staged.workspace !== session.workspace_path || status.session_id !== session.id || status.workspace !== session.workspace_path ||
          !Array.isArray(status.untracked) || working.scope !== "working" || staged.scope !== "staged")
        throw new Error("服务或工作树会话绑定已变化，请刷新列表。");
      setValue({ working: working.diff, staged: staged.diff, untracked: status.untracked, excluded: status.untracked_excluded ?? 0, service: identity.service_id });
    } catch (failure) { if (ticket === epoch.current) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const readFile = async (path: string) => {
    if (!value || busy || !session.project_id || !value.untracked.includes(path)) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, snapshot = value;
    setBusy(true); setFile(null); setError("");
    try {
      const status = await typedIPC.gitStatus({ session_id: session.id });
      if (status.session_id !== session.id || status.workspace !== session.workspace_path || !status.untracked.includes(path)) throw new Error("文件已不在当前未跟踪列表中，请刷新。");
      const result = await typedIPC.readCurrentCodebase({ session_id: session.id, project_id: session.project_id,
        expected_service_id: snapshot.service, path, start_line: 1, end_line: 100 });
      const after = await typedIPC.gitStatus({ session_id: session.id });
      const binding = await typedIPC.getSession(session.id), identity = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (identity.service_id !== snapshot.service || binding.session.id !== session.id || binding.session.project_id !== session.project_id ||
          binding.session.workspace_mode !== "worktree" || binding.session.workspace_path !== session.workspace_path || binding.session.archived ||
          after.session_id !== session.id || after.workspace !== session.workspace_path || !after.untracked.includes(path) || result.path !== path)
        throw new Error("服务、工作树或文件状态已变化，请刷新。");
      setFile({ path, content: result.content, end: result.end_line });
    } catch (failure) { if (ticket === epoch.current) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const readCommits = async () => {
    if (busy || !session.project_id || !session.workspace_path) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setCommitted(null); setMergeResult(null); setError("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      const result = await typedIPC.reviewWorktreeCommits({ session_id: session.id, project_id: session.project_id, expected_service_id: identity.service_id });
      const after = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (!identity.service_id || after.service_id !== identity.service_id || result.service_id !== identity.service_id ||
          result.session_id !== session.id || result.project_id !== session.project_id || result.workspace !== session.workspace_path ||
          !result.target_branch || !result.merge_base || !result.source_head || !result.target_head || !Array.isArray(result.commits))
        throw new Error("服务或工作树绑定已变化，请重新读取。");
      setCommitted(result);
    } catch (failure) { if (ticket === epoch.current) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const mergeReviewed = async () => {
    if (!committed || !session.project_id || busy) return;
    const snapshot = committed, host = ipc.agentBaseUrl;
    const ticket = ++epoch.current;
    setBusy(true); setError(""); setMergeResult(null);
    try {
      const accepted = await requestConfirmation({ title: "合并已审阅提交", description: `将 ${snapshot.source_head} 合并到 ${snapshot.target} 的 ${snapshot.target_branch} 分支（当前 HEAD ${snapshot.target_head}）。如项目配置了验证命令，将先运行该命令。`, confirmLabel: "确认合并" });
      if (!accepted || ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      const identity = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      if (identity.service_id !== snapshot.service_id) throw new Error("服务已变化，请重新比较。");
      const result = await typedIPC.mergeReviewedWorktree({
        session_id: session.id, run_validate: true,
        review: { service_id: snapshot.service_id, project_id: session.project_id, source_head: snapshot.source_head, target_head: snapshot.target_head, target_branch: snapshot.target_branch },
      });
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      setCommitted(null);
      setMergeResult(result);
    } catch (failure) { if (ticket === epoch.current) setError(`合并请求未确认，请先查看仓库状态：${failure instanceof Error ? failure.message : String(failure)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-3 text-xs" aria-label="工作树未提交差异">
    <p className="break-all">工作树会话：{session.id} · {session.workspace_path}</p>
    <p>分别显示已跟踪文件差异和未跟踪文件路径，不包含已提交变更，不能作为完整合并预览。读取不会添加、合并、提交或验证。</p>
    <Button size="sm" disabled={busy} onClick={() => void load()}>读取／刷新差异</Button>{" "}
    <Button size="sm" disabled={busy || !session.project_id} onClick={() => void readCommits()}>比较已提交变更</Button>{" "}
    <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>关闭</Button>
    {error && <p role="alert">{error}</p>}
    {committed && <div className="space-y-1 rounded border border-line p-2"><p className="break-all">目标：{committed.target} · 分支 {committed.target_branch} · HEAD {committed.target_head}</p>
      <p className="break-all">工作树 HEAD：{committed.source_head} · 共同祖先：{committed.merge_base}</p>
      <p>这是共同祖先到工作树 HEAD 的提交差异，目标分支可能也有自己的提交；不包含未提交文件，不预测冲突，也不是验证通过。</p>
      <details><summary>提交摘要（{committed.commits.length}{committed.commits_truncated ? "，已截断" : ""}）</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{committed.commits.join("\n") || "无独有提交"}</pre></details>
      <details open><summary>已提交差异</summary><div className="max-h-80 overflow-auto"><DiffLines text={committed.diff} emptyText="无已提交差异" /></div></details>
      <Button size="sm" disabled={busy} onClick={() => void mergeReviewed()}>合并已审阅提交</Button><p>工作树有未提交内容时不会合并；如发生冲突，Git 合并状态会保留供处理。</p></div>}
    {mergeResult && <div role="status" className="space-y-1 rounded border border-line p-2">
      <p>{mergeResult.merged ? "合并完成。" : mergeReasons[mergeResult.reason ?? ""] ?? `未合并：${mergeResult.reason ?? "原因未知"}`}</p>
      {mergeResult.merge_commit && <p className="break-all">合并提交：{mergeResult.merge_commit}</p>}
      {mergeResult.conflicts && mergeResult.conflicts.length > 0 && <p className="break-all">冲突文件：{mergeResult.conflicts.join("、")}</p>}
      <p>{mergeResult.validation ? "已返回项目验证记录。" : "未返回项目验证记录，不能据此判断检查是否通过。"}</p>
      <details><summary>查看完整结果</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(mergeResult, null, 2)}</pre></details>
    </div>}
    {value && <><details open><summary>未暂存差异</summary><div className="max-h-64 overflow-auto"><DiffLines text={value.working} emptyText="无已跟踪文件差异" /></div></details>
      <details><summary>已暂存差异</summary><div className="max-h-64 overflow-auto"><DiffLines text={value.staged} emptyText="无已暂存差异" /></div></details>
      <details><summary>未跟踪文件（{value.untracked.length}，另排除 {value.excluded}）</summary><p>按需读取前 100 行；敏感或排除路径、超限、空文件和非 UTF-8 文件不可预览，读取需要 read_file 允许规则。</p>
        <ul className="max-h-48 overflow-auto">{value.untracked.map((path) => <li key={path} className="break-all"><button type="button" disabled={busy} onClick={() => void readFile(path)} className="underline">{path}</button></li>)}</ul></details>
      {file && <div><p className="break-all">当前文件 {file.path} · 第 1–{file.end} 行；内容可能随后变化。</p><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{file.content}</pre></div>}</>}
  </div>;
}
