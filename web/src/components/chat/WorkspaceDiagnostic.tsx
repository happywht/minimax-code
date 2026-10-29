import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { WorkspaceCheckResult } from "../../types/ipc";

const sources: Record<WorkspaceCheckResult["source"], string> = {
  session: "会话独立目录", project: "项目根目录", default: "服务进程默认目录", unknown: "未确认",
};
function guidance(result: WorkspaceCheckResult): string {
  if (result.reason === "network_path_not_checked") return "UNC 网络路径未检查，无法判断是否可用。";
  if (result.reason === "check_timed_out") return "目录检查超时，状态未知；请检查本机目录后重试。";
  if (result.reason === "check_unavailable") return "无法完成目录检查，状态未知；请检查目录访问权限后重试。";
  if (result.reason === "execution_boundary_unavailable") return "此会话的执行边界无法确认，请检查会话所属执行环境。";
  if (result.reason === "project_missing") return "所属项目不存在，请重新选择项目或新建会话。";
  if (result.status === "error" && result.source === "session") return "请恢复会话独立目录或新建会话；修改项目根目录不会改变此会话绑定。";
  if (result.status === "error" && result.source === "project") return "请在侧栏项目菜单的“项目验证设置”中修改根目录。";
  if (result.status === "error") return "请检查服务进程默认目录，或为项目设置明确的根目录。";
  if (result.source === "default") return "当前会使用服务进程默认目录；如需指定项目目录，请在项目设置中绑定。";
  return "目录已解析，实际访问权限和后续执行结果仍需在运行时确认。";
}

export function WorkspaceDiagnostic(): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.sessions.find((row) => row.id === session)?.project_id ?? null);
  const [result, setResult] = useState<WorkspaceCheckResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  useEffect(() => {
    setResult(null); setError(""); setBusy(false);
    const generation = epoch;
    return () => { generation.current++; };
  }, [session, project]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setResult(null); setBusy(false); setError("连接变化，请重新检查。"); }), []);
  if (!session) return null;
  const check = async () => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setError(""); setResult(null);
    const current = () => {
      const state = useSessionStore.getState();
      return ticket === epoch.current && host === ipc.agentBaseUrl && state.currentSessionId === session &&
        (state.sessions.find((row) => row.id === session)?.project_id ?? null) === project;
    };
    try {
      const data = await ipc.request<WorkspaceCheckResult>("runtime.workspace_check", { session_id: session, project_id: project });
      if (!current()) return;
      if (data.session_id !== session || data.project_id !== project || !data.service_id) throw new Error("检查结果归属已变化");
      setResult(data);
    } catch (failure) { if (current()) setError(`无法确认目录：${String(failure)}`); }
    finally { if (current()) setBusy(false); }
  };
  return <section className="border-t border-line px-4 py-1 text-xs text-ink-2" data-testid="workspace-diagnostic">
    <button type="button" disabled={busy} onClick={() => void check()}>{busy ? "检查目录中…" : "检查当前会话目录"}</button>
    {error && <p role="status">{error}</p>}
    {result && <div role="status"><p>{result.status === "ok" ? "目录已检查" : result.status === "error" ? "目录不可用" : "目录状态未知"} · {sources[result.source]}{result.path ? `：${result.path}` : ""}</p>
      <p>{guidance(result)}</p><p>未判断映射驱动器是否远程，也未测试写入权限、模型连接或远程执行；目录检查不是操作系统沙盒。</p></div>}
  </section>;
}
