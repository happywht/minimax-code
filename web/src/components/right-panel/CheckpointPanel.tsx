/**
 * CheckpointPanel — session-scoped workspace snapshot list.
 *
 * Wraps the ``checkpoint.*`` IPC namespace so users can create, list,
 * diff, restore and delete workspace checkpoints from the RightPanel.
 */
import { useEffect, useState } from "react";
import { Camera, ChevronDown, ChevronRight, Clock, GitBranch, Trash2, RotateCcw, FileDiff } from "lucide-react";
import { Button, EmptyState, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { Checkpoint } from "../../types/ipc";

export interface CheckpointPanelProps {
  testId?: string;
}

export function CheckpointPanel({ testId = "checkpoint-panel" }: CheckpointPanelProps): JSX.Element {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [expandedDiff, setExpandedDiff] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<Record<string, string>>({});
  const [diffLoading, setDiffLoading] = useState<Record<string, boolean>>({});

  const load = async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const result = await typedIPC.listCheckpoints(sessionId);
      setCheckpoints(result.checkpoints);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load checkpoints", msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [sessionId]);

  const handleCreate = async () => {
    if (!sessionId) {
      toast.error("No session selected");
      return;
    }
    const trimmedLabel = label.trim() || "Checkpoint";
    setCreating(true);
    try {
      await typedIPC.createCheckpoint({
        session_id: sessionId,
        label: trimmedLabel,
        message: message.trim(),
      });
      setLabel("");
      setMessage("");
      toast.success("Checkpoint created", trimmedLabel);
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to create checkpoint", msg);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (ckpt: Checkpoint) => {
    const accepted = await requestConfirmation({
      title: `Delete checkpoint ${ckpt.label}?`,
      description: "This removes the stored snapshot metadata. On-disk untracked copies will be orphaned.",
      confirmLabel: "Delete",
    });
    if (!accepted) return;
    try {
      await typedIPC.deleteCheckpoint(ckpt.id);
      toast.success("Checkpoint deleted", ckpt.label);
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to delete checkpoint", msg);
    }
  };

  const handleRestore = async (ckpt: Checkpoint) => {
    const accepted = await requestConfirmation({
      title: `Restore checkpoint ${ckpt.label}?`,
      description: "This rewinds the working tree to the checkpoint state. Uncommitted changes may be overwritten.",
      confirmLabel: "Restore",
    });
    if (!accepted) return;
    try {
      await typedIPC.restoreCheckpoint(ckpt.id);
      toast.success("Checkpoint restored", ckpt.label);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to restore checkpoint", msg);
    }
  };

  const toggleDiff = async (ckpt: Checkpoint) => {
    if (expandedDiff === ckpt.id) {
      setExpandedDiff(null);
      return;
    }
    setExpandedDiff(ckpt.id);
    if (diffs[ckpt.id] !== undefined) return;
    setDiffLoading((prev) => ({ ...prev, [ckpt.id]: true }));
    try {
      const result = await typedIPC.diffCheckpoint(ckpt.id);
      setDiffs((prev) => ({ ...prev, [ckpt.id]: result.available ? result.patch : "No diff available." }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setDiffs((prev) => ({ ...prev, [ckpt.id]: `Error loading diff: ${msg}` }));
    } finally {
      setDiffLoading((prev) => ({ ...prev, [ckpt.id]: false }));
    }
  };

  return (
    <section data-testid={testId} className="flex flex-col gap-2 text-xs">
      <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-minimax-fg">
          <Camera size={12} className="text-minimax-accent" />
          Checkpoints
        </div>
      </div>

      <div className="space-y-2 px-3 pb-3">
        {sessionId ? (
          <div className="space-y-2 rounded-md border border-minimax-border bg-minimax-bg/40 p-2">
            <input
              type="text"
              placeholder="Checkpoint label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-[11px] outline-none focus:border-minimax-accent"
              data-testid={`${testId}-label`}
            />
            <input
              type="text"
              placeholder="Optional message"
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
                <span className="ml-1">Create</span>
              </Button>
            </div>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-minimax-border p-3 text-center text-[11px] text-minimax-muted">
            选择会话后管理 Checkpoints
          </div>
        )}

        {loading && checkpoints.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-4 text-[11px] text-minimax-muted">
            <Spinner size={12} /> Loading checkpoints…
          </div>
        ) : checkpoints.length === 0 ? (
          <EmptyState
            title="暂无 Checkpoint"
            hint="点击 Create 保存当前工作区快照。"
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
                        {ckpt.tracked_files.length + ckpt.untracked_files.length} files
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void toggleDiff(ckpt)}
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
                    {diffLoading[ckpt.id] ? (
                      <div className="flex items-center gap-2 text-[11px] text-minimax-muted">
                        <Spinner size={10} /> Loading diff…
                      </div>
                    ) : (
                      <pre className="max-h-40 overflow-auto rounded bg-minimax-bg p-2 font-mono text-[11px] text-minimax-fg">
                        {diffs[ckpt.id] || "No diff available."}
                      </pre>
                    )}
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
