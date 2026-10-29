/**
 * CheckpointPanel — session-scoped workspace snapshot list.
 *
 * Wraps the ``checkpoint.*`` IPC namespace so users can create, list,
 * diff, restore and delete workspace checkpoints from the RightPanel.
 */
import { useCallback, useEffect, useState } from "react";
import { Camera, ChevronDown, ChevronRight, Clock, GitBranch, Trash2, RotateCcw, FileDiff } from "lucide-react";
import { Button, DiffLines, EmptyState, ErrorBanner, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { Checkpoint } from "../../types/ipc";
import { strings } from "../../ui/strings";

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [legacyPaths, setLegacyPaths] = useState<Record<string, string>>({});
  const [legacyConfirmed, setLegacyConfirmed] = useState<Record<string, boolean>>({});
  const [restoreDetails, setRestoreDetails] = useState<Record<string, string[]>>({});

  const legacyOptions = (ckpt: Checkpoint) => ckpt.source_root ? undefined : {
    cwd: (legacyPaths[ckpt.id] ?? "").trim(), legacy_source_confirmed: true,
  };
  const legacyReady = (ckpt: Checkpoint) => Boolean(ckpt.source_root ||
    ((legacyPaths[ckpt.id] ?? "").trim() && legacyConfirmed[ckpt.id]));

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const result = await typedIPC.listCheckpoints(sessionId);
      setCheckpoints(result.checkpoints);
    } catch (err) {
      // Inline banner (not a toast): a failed load must stay visible so
      // the empty list below is never read as "no checkpoints yet".
      const msg = err instanceof Error ? err.message : String(err);
      setLoadError(`${strings.rightPanel.checkpoint.loadFailed}: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

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
      await load();
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
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.rightPanel.checkpoint.deleteFailed, msg);
    }
  };

  const handleRestore = async (ckpt: Checkpoint) => {
    if (!legacyReady(ckpt)) return;
    const accepted = await requestConfirmation({
      title: strings.rightPanel.checkpoint.restoreTitle(ckpt.label),
      description: strings.rightPanel.checkpoint.restoreDescription,
      confirmLabel: strings.rightPanel.checkpoint.restoreConfirm,
    });
    if (!accepted) return;
    try {
      const { result } = await typedIPC.restoreCheckpoint(ckpt.id, legacyOptions(ckpt));
      const details = [...result.warnings];
      if (result.skipped_existing.length) details.push(`未覆盖已有文件：${result.skipped_existing.join(", ")}`);
      if (!result.restored) details.unshift("没有文件被恢复；检查点可能为空或所需内容不可用。");
      setRestoreDetails((prev) => ({ ...prev, [ckpt.id]: details }));
      if (details.length) toast.error("恢复未确认完整", "请查看检查点下方的详情。");
      else toast.success(strings.rightPanel.checkpoint.restored, ckpt.label);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.rightPanel.checkpoint.restoreFailed, msg);
    }
  };

  const toggleDiff = async (ckpt: Checkpoint) => {
    if (!legacyReady(ckpt)) return;
    if (expandedDiff === ckpt.id) {
      setExpandedDiff(null);
      return;
    }
    setExpandedDiff(ckpt.id);
    if (diffs[ckpt.id] !== undefined) return;
    setDiffLoading((prev) => ({ ...prev, [ckpt.id]: true }));
    try {
      const result = await typedIPC.diffCheckpoint(ckpt.id, legacyOptions(ckpt));
      setDiffs((prev) => ({
        ...prev,
        [ckpt.id]: result.available ? result.patch : strings.rightPanel.checkpoint.noDiff,
      }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setDiffs((prev) => ({
        ...prev,
        [ckpt.id]: strings.rightPanel.checkpoint.diffLoadError(msg),
      }));
    } finally {
      setDiffLoading((prev) => ({ ...prev, [ckpt.id]: false }));
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
            选择会话后管理 Checkpoints
          </div>
        )}

        {loadError && <ErrorBanner message={loadError} onRetry={() => void load()} testId={`${testId}-error`} />}

        {loading && checkpoints.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-4 text-[11px] text-minimax-muted">
            <Spinner size={12} /> {strings.rightPanel.checkpoint.loading}
          </div>
        ) : checkpoints.length === 0 && !loadError ? (
          <EmptyState
            title="暂无 Checkpoint"
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
                    {restoreDetails[ckpt.id]?.length > 0 && (
                      <div role="status" data-testid={`${testId}-restore-details-${ckpt.id}`} className="mt-1 text-[11px] text-status-warning">
                        <p>恢复未确认完整：</p>
                        {restoreDetails[ckpt.id].map((detail, index) => <p key={index}>{detail}</p>)}
                      </div>
                    )}
                    {!ckpt.source_root && (
                      <div className="mt-1 text-[11px] text-status-warning">
                        <p>旧检查点的原目录未知。请核实创建时使用的工作目录；系统无法验证该来源。</p>
                        <label>原工作目录
                          <input className="w-full text-minimax-fg bg-minimax-bg" value={legacyPaths[ckpt.id] ?? ""}
                            onChange={(event) => {
                              setLegacyPaths((prev) => ({ ...prev, [ckpt.id]: event.target.value }));
                              setLegacyConfirmed((prev) => ({ ...prev, [ckpt.id]: false }));
                              setDiffs((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
                            }} />
                        </label>
                        <label><input type="checkbox" checked={Boolean(legacyConfirmed[ckpt.id])}
                          onChange={(event) => setLegacyConfirmed((prev) => ({ ...prev, [ckpt.id]: event.target.checked }))} />
                          我已核实此目录是创建检查点时使用的目录
                        </label>
                      </div>
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
                      onClick={() => void toggleDiff(ckpt)}
                      disabled={!legacyReady(ckpt)}
                      data-testid={`${testId}-diff-${ckpt.id}`}
                      icon={expandedDiff === ckpt.id ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                    >
                      <FileDiff size={10} />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleRestore(ckpt)}
                      disabled={!legacyReady(ckpt)}
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
                        <Spinner size={10} /> {strings.rightPanel.checkpoint.loadingDiff}
                      </div>
                    ) : (
                      <div
                        className="max-h-40 overflow-auto rounded bg-minimax-bg p-2"
                        data-testid={`${testId}-diff-body-${ckpt.id}`}
                      >
                        <DiffLines
                          text={diffs[ckpt.id] ?? ""}
                          emptyText={strings.rightPanel.checkpoint.noDiff}
                        />
                      </div>
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
