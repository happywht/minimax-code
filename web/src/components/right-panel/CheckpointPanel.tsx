/**
 * CheckpointPanel — session-scoped workspace snapshot list.
 *
 * Wraps the ``checkpoint.*`` IPC namespace so users can create, list,
 * diff, restore and delete workspace checkpoints from the RightPanel.
 * List / diff / loading state lives in ``useCheckpointStore`` so it
 * survives tab switches; only the form drafts stay local.
 */
import { useEffect, useState } from "react";
import { Camera, ChevronDown, ChevronRight, Clock, GitBranch, Trash2, RotateCcw, FileDiff } from "lucide-react";
import { Button, DiffLines, EmptyState, ErrorBanner, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { useSessionStore, useCheckpointStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { Checkpoint } from "../../types/ipc";
import { strings } from "../../ui/strings";

export interface CheckpointPanelProps {
  testId?: string;
}

export function CheckpointPanel({ testId = "checkpoint-panel" }: CheckpointPanelProps): JSX.Element {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const checkpoints = useCheckpointStore((s) => (sessionId ? s.bySession[sessionId] : undefined)) ?? [];
  const loading = useCheckpointStore((s) => (sessionId ? !!s.loading[sessionId] : false));
  const loadError = useCheckpointStore((s) => (sessionId ? s.loadError[sessionId] : null)) ?? null;
  const expandedDiff = useCheckpointStore((s) => s.expandedDiffId);
  const load = useCheckpointStore((s) => s.load);
  const invalidate = useCheckpointStore((s) => s.invalidate);
  const toggleExpand = useCheckpointStore((s) => s.toggleExpand);
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (sessionId) void load(sessionId);
  }, [sessionId, load]);

  const handleCreate = async () => {
    if (!sessionId) {
      toast.error(strings.rightPanel.checkpoint.noSession);
      return;
    }
    const trimmedLabel = label.trim() || strings.rightPanel.checkpoint.defaultLabel;
    setCreating(true);
    try {
      await typedIPC.createCheckpoint({
        session_id: sessionId,
        label: trimmedLabel,
        message: message.trim(),
      });
      setLabel("");
      setMessage("");
      toast.success(strings.rightPanel.checkpoint.created, trimmedLabel);
      await invalidate(sessionId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.rightPanel.checkpoint.createFailed, msg);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (ckpt: Checkpoint) => {
    const accepted = await requestConfirmation({
      title: strings.rightPanel.checkpoint.deleteTitle(ckpt.label),
      description: strings.rightPanel.checkpoint.deleteDescription,
      confirmLabel: strings.rightPanel.checkpoint.deleteConfirm,
    });
    if (!accepted) return;
    try {
      await typedIPC.deleteCheckpoint(ckpt.id);
      toast.success(strings.rightPanel.checkpoint.deleted, ckpt.label);
      if (sessionId) await invalidate(sessionId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.rightPanel.checkpoint.deleteFailed, msg);
    }
  };

  const handleRestore = async (ckpt: Checkpoint) => {
    const accepted = await requestConfirmation({
      title: strings.rightPanel.checkpoint.restoreTitle(ckpt.label),
      description: strings.rightPanel.checkpoint.restoreDescription,
      confirmLabel: strings.rightPanel.checkpoint.restoreConfirm,
    });
    if (!accepted) return;
    try {
      await typedIPC.restoreCheckpoint(ckpt.id);
      toast.success(strings.rightPanel.checkpoint.restored, ckpt.label);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.rightPanel.checkpoint.restoreFailed, msg);
    }
  };

  return (
    <section data-testid={testId} className="flex flex-col gap-2 text-xs">
      <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-minimax-fg">
          <Camera size={12} className="text-minimax-accent" />
          {strings.rightPanel.checkpoint.title}
        </div>
      </div>

      <div className="space-y-2 px-3 pb-3">
        {sessionId ? (
          <div className="space-y-2 rounded-md border border-minimax-border bg-minimax-bg/40 p-2">
            <input
              type="text"
              placeholder={strings.rightPanel.checkpoint.labelPlaceholder}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] outline-none focus:border-minimax-accent"
              data-testid={`${testId}-label`}
            />
            <input
              type="text"
              placeholder={strings.rightPanel.checkpoint.messagePlaceholder}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] outline-none focus:border-minimax-accent"
              data-testid={`${testId}-message`}
            />
            <div className="flex justify-end">
              <Button
                size="sm"
                variant="primary"
                onClick={() => void handleCreate()}
                disabled={creating}
                data-testid={`${testId}-create`}
              >
                {creating ? <Spinner size={10} /> : <Camera size={10} />}
                <span className="ml-1">{strings.rightPanel.checkpoint.create}</span>
              </Button>
            </div>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-minimax-border p-3 text-center text-[11px] text-minimax-muted">
            {strings.rightPanel.checkpoint.selectSessionHint}
          </div>
        )}

        {loadError && <ErrorBanner message={`${strings.rightPanel.checkpoint.loadFailed}: ${loadError}`} onRetry={() => sessionId && void load(sessionId, true)} testId={`${testId}-error`} />}

        {loading && checkpoints.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-4 text-[11px] text-minimax-muted">
            <Spinner size={12} /> {strings.rightPanel.checkpoint.loading}
          </div>
        ) : checkpoints.length === 0 && !loadError ? (
          <EmptyState
            title={strings.rightPanel.checkpoint.emptyTitle}
            hint={strings.rightPanel.checkpoint.emptyHint}
          />
        ) : (
          <ul className="space-y-1.5" data-testid={`${testId}-list`}>
            {checkpoints.map((ckpt) => (
              <li
                key={ckpt.id}
                className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
                data-testid={`${testId}-row-${ckpt.id}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-minimax-fg">{ckpt.label}</div>
                    {ckpt.message && (
                      <div className="truncate text-[11px] text-minimax-muted">{ckpt.message}</div>
                    )}
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-minimax-muted">
                      <span className="flex items-center gap-0.5" title={ckpt.created_at}>
                        <Clock size={10} />
                        {new Date(ckpt.created_at).toLocaleString()}
                      </span>
                      {ckpt.branch && (
                        <span className="flex items-center gap-0.5">
                          <GitBranch size={10} />
                          {ckpt.branch}
                        </span>
                      )}
                      <span>
                        {strings.rightPanel.checkpoint.fileCount(
                          ckpt.tracked_files.length + ckpt.untracked_files.length,
                        )}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => toggleExpand(ckpt.id)}
                      data-testid={`${testId}-diff-${ckpt.id}`}
                      icon={expandedDiff === ckpt.id ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                    >
                      <FileDiff size={10} />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleRestore(ckpt)}
                      data-testid={`${testId}-restore-${ckpt.id}`}
                    >
                      <RotateCcw size={10} />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleDelete(ckpt)}
                      data-testid={`${testId}-delete-${ckpt.id}`}
                    >
                      <Trash2 size={10} />
                    </Button>
                  </div>
                </div>

                {expandedDiff === ckpt.id && (
                  <div className="mt-2 border-t border-minimax-border pt-2">
                    <CheckpointDiffBody checkpointId={ckpt.id} testId={testId} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/**
 * The expanded diff body subscribes to the per-checkpoint loading flag
 * on its own so the panel above doesn't re-render while every diff
 * fetch resolves.
 */
function CheckpointDiffBody({ checkpointId, testId }: { checkpointId: string; testId: string }): JSX.Element {
  const text = useCheckpointStore((s) => s.diffs[checkpointId]);
  const diffLoading = useCheckpointStore((s) => !!s.diffLoading[checkpointId]);
  if (diffLoading || text === undefined) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-minimax-muted">
        <Spinner size={10} /> {strings.rightPanel.checkpoint.loadingDiff}
      </div>
    );
  }
  return (
    <div
      className="max-h-40 overflow-auto rounded bg-minimax-bg p-2"
      data-testid={`${testId}-diff-body-${checkpointId}`}
    >
      <DiffLines
        text={text}
        emptyText={strings.rightPanel.checkpoint.noDiff}
      />
    </div>
  );
}
