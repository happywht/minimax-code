import { useRef, useState } from "react";
import { typedIPC } from "../../ipc";
import type { AgentRun } from "../../types/ipc";
import { Button } from "../../ui";

export function RunRecoveryActions({ run, refresh }: { run: AgentRun; refresh: () => Promise<void> }): JSX.Element | null {
  const [reviewed, setReviewed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [resumed, setResumed] = useState(false);
  const resumeKey = useRef<string | null>(null);
  const resumable = ["interrupted", "failed", "cancelled"].includes(run.status);
  const active = ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status);
  if (!run.metadata?.model_snapshot || typeof run.metadata.workspace_snapshot !== "string") return null;
  if (!resumable && !active) return null;

  const act = async () => {
    setPending(true);
    setError(null);
    try {
      if (resumable) {
        resumeKey.current ??= crypto.randomUUID();
        await typedIPC.resumeRun(run.id, resumeKey.current, reviewed);
        setResumed(true);
      } else {
        const reply = await typedIPC.cancelRun(run.id);
        if (!reply.requested && !["completed", "failed", "cancelled", "interrupted"].includes(reply.run.status)) {
          throw new Error("任务未在当前服务中执行，请刷新并检查状态。");
        }
        setCancelRequested(reply.requested);
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-2 space-y-2 text-xs text-minimax-muted" data-testid="run-recovery-actions">
      {resumable && (
        <>
          <p>继续会创建关联的新运行。请先核对已完成的步骤与文件，避免重复产生修改。</p>
          {run.started_at && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
              已核对历史步骤与当前文件
            </label>
          )}
        </>
      )}
      <Button size="sm" variant="secondary" onClick={() => void act()}
        disabled={pending || resumed || (!resumable && cancelRequested) || (resumable && !!run.started_at && !reviewed)}>
        {pending ? "处理中…" : resumed ? "已创建接续运行" : resumable ? "继续此任务" : cancelRequested ? "正在取消…" : "取消此任务"}
      </Button>
      {error && <p role="alert" className="text-status-error">{error}</p>}
    </div>
  );
}
