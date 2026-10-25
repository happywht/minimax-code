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
  const [budgetTokens, setBudgetTokens] = useState("");
  const codex = run.metadata?.engine === "codex";
  const paused = run.status === "budget_paused";
  const resumable = ["interrupted", "failed", "cancelled", "budget_paused"].includes(run.status);
  const requiresReview = !!run.started_at && (codex || !paused);
  const active = ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status);
  if (typeof run.metadata?.workspace_snapshot !== "string") return null;
  if (codex ? !run.metadata.codex_thread_id : !run.metadata.model_snapshot) return null;
  if (!resumable && !active) return null;

  const act = async () => {
    setPending(true);
    setError(null);
    try {
      if (resumable) {
        resumeKey.current ??= crypto.randomUUID();
        await typedIPC.resumeRun(run.id, resumeKey.current, reviewed, paused ? Number(budgetTokens) : undefined);
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
          {codex && <p>Codex 中断或预算暂停可能留下外部副作用；恢复前必须核对，不能视为安全调用边界。</p>}
          {paused && <label>恢复 token 上限（整项任务累计，含历史与子任务）<input aria-label="恢复 token 上限" type="number" min="1" value={budgetTokens} onChange={(event) => { setBudgetTokens(event.target.value); resumeKey.current = null; }} /></label>}
          {requiresReview && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
              已核对历史步骤与当前文件
            </label>
          )}
        </>
      )}
      <Button size="sm" variant="secondary" onClick={() => void act()}
        disabled={pending || resumed || (!resumable && cancelRequested) || (resumable && requiresReview && !reviewed) || (paused && (!budgetTokens || Number(budgetTokens) <= 0))}>
        {pending ? "处理中…" : resumed ? "已创建接续运行" : resumable ? "继续此任务" : cancelRequested ? "正在取消…" : "取消此任务"}
      </Button>
      {error && <p role="alert" className="text-status-error">{error}</p>}
    </div>
  );
}
