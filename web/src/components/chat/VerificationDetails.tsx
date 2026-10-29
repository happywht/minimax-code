import type { VerificationEvidence } from "../../types/ipc";

/** Show only the evidence actually saved for this run; no inferred pass. */
export function VerificationDetails({ evidence }: { evidence?: VerificationEvidence }): JSX.Element | null {
  const result = evidence?.version === 1 ? evidence.validation : undefined;
  if (!result) return null;
  return <details className="mt-2 rounded border border-line p-2 text-xs" data-testid="verification-details">
    <summary>查看验证命令与输出</summary>
    <p className="mt-2">命令：<code className="break-all">{result.command || "未记录"}</code></p>
    <p>退出码：{result.exit_code == null ? "未知" : result.exit_code} · {result.timed_out ? "已超时" : result.exit_code == null ? "未取得结果" : result.ok && result.exit_code === 0 ? "命令通过" : "未通过"}</p>
    {result.error && <p role="status" className="break-words text-status-warning">{result.error}</p>}
    {(result.stdout || result.stdout_truncated) && <div><p>标准输出{result.stdout_truncated ? "（已截断）" : ""}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{result.stdout}</pre></div>}
    {(result.stderr || result.stderr_truncated) && <div><p>标准错误{result.stderr_truncated ? "（已截断）" : ""}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{result.stderr}</pre></div>}
  </details>;
}
