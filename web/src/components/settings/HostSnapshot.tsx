import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import type { HostExecResult, RemoteHost } from "../../types/ipc";
import { Button } from "../../ui";

// Fixed read-only commands; never interpolate host names or user input into the shell.
const SNAPSHOT_COMMAND = `failed=0
printf '\n== Uptime ==\n'
uptime || failed=1
printf '\n== Root filesystem ==\n'
df -h / || failed=1
printf '\n== Memory ==\n'
free -h || failed=1
printf '\n== Top processes (PID, name, CPU%%, memory%%) ==\n'
processes=$(ps -eo pid,comm,pcpu,pmem --sort=-pcpu) || failed=1
printf '%s\n' "$processes" | head -n 11
exit "$failed"`;

export function HostSnapshot({ host }: { host: RemoteHost }): JSX.Element {
  const [result, setResult] = useState<HostExecResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const identity = JSON.stringify([host.id, host.hostname, host.username, host.port, host.identity_file, host.enabled]);
  const base = ipc.agentBaseUrl;

  useEffect(() => {
    epoch.current++;
    setResult(null); setError(""); setBusy(false);
    return () => { epoch.current++; };
  }, [identity, base]);
  useEffect(() => ipc.onSideCar(() => {
    epoch.current++; setResult(null); setError(""); setBusy(false);
  }), []);

  const snapshot = async () => {
    if (!host.enabled || busy) return;
    const ticket = ++epoch.current;
    setResult(null); setError(""); setBusy(true);
    try {
      const response = await typedIPC.execHost({ host_id: host.id, command: SNAPSHOT_COMMAND, timeout_s: 15 });
      if (ticket === epoch.current && ipc.agentBaseUrl === base) setResult(response);
    } catch (err) {
      if (ticket === epoch.current && ipc.agentBaseUrl === base) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (ticket === epoch.current && ipc.agentBaseUrl === base) setBusy(false);
    }
  };

  return <details className="rounded border border-line p-2">
    <summary>主机运行快照</summary>
    <p className="text-xs text-ink-2">仅在点击时通过独立 SSH 读取运行时间、根分区、内存和 CPU 占用最高的进程名（不显示命令行）。需要 Linux 的 uptime、df、free、ps 和 head；不判断任务是否持续运行。快照可能包含敏感的进程名称。</p>
    <Button type="button" size="sm" disabled={busy || !host.enabled} onClick={() => void snapshot()}>读取此主机快照</Button>
    {busy && <p role="status" className="text-xs">正在读取快照…</p>}
    {error && <p role="alert" className="break-words text-xs text-status-error">快照读取失败：{error}。无法据此判断主机是否离线。</p>}
    {result && <div className="space-y-1 text-xs">
      <p role="status">{result.timed_out ? "读取超时，结果可能不完整" : result.ok ? "读取完成" : "读取失败或部分命令失败"} · 退出码：{result.exit_code ?? "未知"}。这是读取时的快照，不判断主机是否离线或任务是否持续运行。</p>
      {result.stdout && <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-2 p-2">{result.stdout}</pre>}
      {result.stderr && <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-status-error">{result.stderr}</pre>}
    </div>}
  </details>;
}
