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
import type { Checkpoint, CheckpointDiffResult } from "../../types/ipc";
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
  const [previews, setPreviews] = useState<Record<string, NonNullable<CheckpointDiffResult["untracked_preview"]>>>({});
  const [restoring, setRestoring] = useState(false);
  const [rebindPaths, setRebindPaths] = useState<Record<string, string>>({});
  const [rebindTrusted, setRebindTrusted] = useState<Record<string, boolean>>({});
  const [rebinding, setRebinding] = useState<string | null>(null);

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

  const handleApplyCheckpoint = async (ckpt: Checkpoint) => {
    if (!legacyReady(ckpt) || restoring || !previews[ckpt.id]) return;
    const accepted = await requestConfirmation({
      title: `应用检查点「${ckpt.label}」`,
      description: "这会尝试应用 Git stash 并复制未跟踪文件，不是精确撤销。请先保护当前修改；预检不会锁定工作区，并发修改或 Git 冲突可能导致部分结果。已有未跟踪文件不会被覆盖。",
      confirmLabel: "应用检查点",
    });
    if (!accepted) return;
    setRestoring(true);
    try {
      const { result } = await typedIPC.restoreCheckpoint(ckpt.id, legacyOptions(ckpt));
      const details = [...result.warnings];
      if (result.skipped_existing.length) details.push(`未覆盖已有文件：${result.skipped_existing.join(", ")}`);
      if (!result.restored) details.unshift("没有文件被恢复；检查点可能为空或所需内容不可用。");
      setRestoreDetails((prev) => ({ ...prev, [ckpt.id]: details }));
      if (details.length) toast.error("应用未确认完整", "请查看检查点下方的详情。");
      else toast.success("检查点已应用", ckpt.label);
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
      setExpandedDiff(null);
    } catch (err) {
      toast.error("检查点应用失败", err instanceof Error ? err.message : String(err));
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
    } finally {
      setRestoring(false);
    }
  };

  const handleRestoreFile = async (ckpt: Checkpoint, path: string) => {
    if (!legacyReady(ckpt) || restoring) return;
    const accepted = await requestConfirmation({
      title: `恢复文件 ${path}`,
      description: "仅创建当前不存在的文件；已有文件不会被覆盖。预览后文件状态可能发生变化，恢复时会再次检查。",
      confirmLabel: "恢复此文件",
    });
    if (!accepted) return;
    setRestoring(true);
    try {
      const { result } = await typedIPC.restoreCheckpoint(ckpt.id, { ...legacyOptions(ckpt), untracked_file: path });
      const details = [...result.warnings];
      if (result.skipped_existing.length) details.push(`目标已存在，未覆盖：${result.skipped_existing.join(", ")}`);
      if (!result.restored && !details.length) details.push("没有文件被恢复。");
      setRestoreDetails((prev) => ({ ...prev, [ckpt.id]: details }));
      if (details.length) toast.error("文件未恢复", details.join("；"));
      else toast.success("文件已恢复", path);
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
      setExpandedDiff(null);
    } catch (err) {
      toast.error("文件恢复失败", err instanceof Error ? err.message : String(err));
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
    } finally {
      setRestoring(false);
    }
  };

  const handleRebind = async (ckpt: Checkpoint) => {
    const cwd = (rebindPaths[ckpt.id] ?? "").trim();
    if (!cwd || !rebindTrusted[ckpt.id] || rebinding) return;
    const accepted = await requestConfirmation({
      title: `重新绑定检查点「${ckpt.label}」`,
      description: `确认将此检查点绑定到 ${cwd}？请先核实可信数据包并导入其 Git 对象。系统无法证明目标是原仓库，不会修改项目或会话。`,
      confirmLabel: "确认重新绑定",
    });
    if (!accepted) return;
    setRebinding(ckpt.id);
    try {
      const { checkpoint } = await typedIPC.rebindCheckpoint(ckpt.id, {
        cwd, confirm_rebind: true, confirm_trusted_bundle: true,
      });
      setCheckpoints((prev) => prev.map((row) => row.id === ckpt.id ? checkpoint : row));
      setRebindTrusted((prev) => ({ ...prev, [ckpt.id]: false }));
      setDiffs((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
      setExpandedDiff(null);
      toast.success("检查点已重新绑定", ckpt.label);
    } catch (err) {
      toast.error("检查点重新绑定失败", err instanceof Error ? err.message : String(err));
    } finally {
      setRebinding(null);
    }
  };

  const toggleDiff = async (ckpt: Checkpoint) => {
    if (!legacyReady(ckpt)) return;
    if (expandedDiff === ckpt.id) {
      setExpandedDiff(null);
      return;
    }
    setExpandedDiff(ckpt.id);
    setDiffLoading((prev) => ({ ...prev, [ckpt.id]: true }));
    try {
      const result = await typedIPC.diffCheckpoint(ckpt.id, legacyOptions(ckpt));
      setDiffs((prev) => ({
        ...prev,
        [ckpt.id]: result.available ? result.patch : strings.rightPanel.checkpoint.noDiff,
      }));
      setPreviews((prev) => {
        const next = { ...prev };
        if (result.untracked_preview) next[ckpt.id] = result.untracked_preview;
        else delete next[ckpt.id];
        return next;
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setDiffs((prev) => ({
        ...prev,
        [ckpt.id]: strings.rightPanel.checkpoint.diffLoadError(msg),
      }));
      setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
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
                              setPreviews((prev) => { const next = { ...prev }; delete next[ckpt.id]; return next; });
                            }} />
                        </label>
                        <label><input type="checkbox" checked={Boolean(legacyConfirmed[ckpt.id])}
                          onChange={(event) => setLegacyConfirmed((prev) => ({ ...prev, [ckpt.id]: event.target.checked }))} />
                          我已核实此目录是创建检查点时使用的目录
                        </label>
                      </div>
                    )}
                    {ckpt.git_stash_ref && (
                      <details className="mt-1 text-[11px] text-minimax-muted">
                        <summary>迁移后重新绑定</summary>
                        <p>先将会话指向新工作区并导入可信数据包中的 Git 对象。校验不能证明仓库身份。</p>
                        <label>新仓库根目录
                          <input className="w-full text-minimax-fg bg-minimax-bg" value={rebindPaths[ckpt.id] ?? ""}
                            onChange={(event) => {
                              setRebindPaths((prev) => ({ ...prev, [ckpt.id]: event.target.value }));
                              setRebindTrusted((prev) => ({ ...prev, [ckpt.id]: false }));
                            }} />
                        </label>
                        <label><input type="checkbox" checked={Boolean(rebindTrusted[ckpt.id])}
                          onChange={(event) => setRebindTrusted((prev) => ({ ...prev, [ckpt.id]: event.target.checked }))} />
                          我已核实数据包可信且目标仓库正确
                        </label>
                        <Button size="sm" variant="secondary" data-testid={`${testId}-rebind-${ckpt.id}`}
                          disabled={!rebindTrusted[ckpt.id] || !(rebindPaths[ckpt.id] ?? "").trim() || Boolean(rebinding)}
                          onClick={() => void handleRebind(ckpt)}>确认目标并重新绑定</Button>
                      </details>
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
                        <p className="text-[11px] text-minimax-muted">已跟踪文件显示的是检查点捕获的补丁，不是当前工作区的撤销预览。未跟踪文件状态可能随时变化，恢复时会重新检查。</p>
                        <DiffLines
                          text={diffs[ckpt.id] ?? ""}
                          emptyText={strings.rightPanel.checkpoint.noDiff}
                        />
                        <Button size="sm" variant="secondary" disabled={restoring || !previews[ckpt.id]} data-testid={`${testId}-restore-${ckpt.id}`} onClick={() => void handleApplyCheckpoint(ckpt)}>应用检查点（非精确撤销）</Button>
                        <div className="mt-2 text-[11px]">
                          <p>未跟踪文件恢复预览（关闭后重新打开可刷新）：</p>
                          {previews[ckpt.id]?.map(({ path, status }) => (
                            <div key={path} className="flex items-center justify-between gap-2">
                              <span className="break-all">{path} — {status === "available" ? "目标不存在" : status === "existing" ? "目标已存在" : status === "missing_snapshot" ? "快照缺失" : "路径不安全"}</span>
                              {status === "available" && <Button size="sm" variant="secondary" disabled={restoring} onClick={() => void handleRestoreFile(ckpt, path)}><RotateCcw size={10} />恢复此文件</Button>}
                            </div>
                          ))}
                          {previews[ckpt.id]?.length === 0 && <p>没有未跟踪文件。</p>}
                          {!previews[ckpt.id] && <p>未取得文件状态，无法恢复。请重新打开预览。</p>}
                        </div>
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
