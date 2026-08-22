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
import { useCallback, useEffect, useState } from "react";
import { GitBranch, RefreshCw, Trash2 } from "lucide-react";
import { Button, EmptyState, IconButton, Spinner } from "../../ui";
import { strings } from "../../ui/strings";
import { typedIPC } from "../../ipc";
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
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
