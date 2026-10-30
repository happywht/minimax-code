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
import type { Session } from "../../types/ipc";
import { TabHeader } from "./fields";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { toast } from "../layout/ErrorBoundary";

export { WorktreesTab };

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
  const [value, setValue] = useState<{ working: string; staged: string } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => { const stop = ipc.onSideCar(() => { ++epoch.current; setValue(null); setBusy(false); setError("连接变化，请重新读取。"); }); return () => { ++epoch.current; stop(); }; }, []);
  const load = async () => {
    if (busy || !session.workspace_path) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setValue(null); setError("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      const before = await typedIPC.getSession(session.id);
      const working = await typedIPC.gitDiff({ scope: "working", session_id: session.id });
      const staged = await typedIPC.gitDiff({ scope: "staged", session_id: session.id });
      const after = await typedIPC.getSession(session.id);
      const identityAfter = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host) return;
      const matches = (row: Session) => row.id === session.id && row.project_id === session.project_id && row.engine === "native" &&
        row.workspace_mode === "worktree" && row.workspace_path === session.workspace_path && !row.archived;
      if (!identity.service_id || identity.service_id !== identityAfter.service_id || !matches(before.session) || !matches(after.session) ||
          working.session_id !== session.id || staged.session_id !== session.id || working.workspace !== session.workspace_path ||
          staged.workspace !== session.workspace_path || working.scope !== "working" || staged.scope !== "staged")
        throw new Error("服务或工作树会话绑定已变化，请刷新列表。");
      setValue({ working: working.diff, staged: staged.diff });
    } catch (failure) { if (ticket === epoch.current) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-3 text-xs" aria-label="工作树未提交差异">
    <p className="break-all">工作树会话：{session.id} · {session.workspace_path}</p>
    <p>仅显示读取时已跟踪文件的未暂存和已暂存差异；不包含未跟踪文件或已提交变更，不能作为完整合并预览。读取不会合并、提交或验证。</p>
    <Button size="sm" disabled={busy} onClick={() => void load()}>读取／刷新差异</Button>{" "}
    <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>关闭</Button>
    {error && <p role="alert">读取失败：{error}</p>}
    {value && <><details open><summary>未暂存差异</summary><div className="max-h-64 overflow-auto"><DiffLines text={value.working} emptyText="无已跟踪文件差异" /></div></details>
      <details><summary>已暂存差异</summary><div className="max-h-64 overflow-auto"><DiffLines text={value.staged} emptyText="无已暂存差异" /></div></details></>}
  </div>;
}
