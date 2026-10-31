import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, RunVerifyProjectParams, RunWorkspaceDiffResult } from "../../types/ipc";
import { Button } from "../../ui";
import { requestConfirmation } from "../modals/ConfirmationDialog";

/** Explicit, independently recorded validation of the current workspace. */
export function RunManualVerification({ run, review }: { run: AgentRun; review: RunWorkspaceDiffResult }): JSX.Element | null {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [validation, setValidation] = useState<{ id: string; status: string; evidence?: unknown } | null>(null);
  const base = ipc.agentBaseUrl, key = `minimax:project-validation:${JSON.stringify([base, review.service_id, review.project_id, review.session_id, run.id])}`;
  const pending = () => { try { return localStorage.getItem(key); } catch { return "unavailable"; } };
  const [uncertain, setUncertain] = useState(() => !!pending());
  const epoch = useRef(0), inFlight = useRef(false);
  useEffect(() => { ++epoch.current; setValidation(null); setUncertain(!!pending()); setNotice(""); return () => { ++epoch.current; }; }, [key]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setValidation(null); setUncertain(!!pending()); setNotice("连接已变化；请核对原服务和验证运行，勿重复提交。"); }), [key]);
  const eligible = ["completed", "failed", "interrupted", "cancelled", "budget_paused"].includes(run.status) &&
    ["chat", "execute"].includes(run.mode) && !run.metadata?.manual_project_validation && !run.metadata?.manual_team_validation;
  if (!eligible || !review.project_id) return null;
  const project = review.project_id, session = review.session_id;
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === base &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const start = async () => {
    if (inFlight.current || uncertain || pending() || ipc.isMock) return;
    inFlight.current = true; setBusy(true); setNotice("");
    const ticket = ++epoch.current;
    try {
      const [identity, projects, snapshot, fresh] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.listProjects(),
        typedIPC.getRunSteps(run.id), typedIPC.gitRunWorkspaceDiff(run.id, session)]);
      if (!current(ticket)) return;
      const configured = projects.projects.find((item) => item.id === project && !item.archived);
      const command = configured?.validate_command?.trim();
      if (!command || command.length > 500 || identity.service_id !== review.service_id || fresh.service_id !== review.service_id ||
          fresh.run_id !== run.id || fresh.session_id !== session || fresh.project_id !== project || fresh.workspace !== review.workspace ||
          fresh.workspace_identity !== review.workspace_identity || fresh.diff !== review.diff || fresh.staged_diff !== review.staged_diff ||
          JSON.stringify(fresh.untracked) !== JSON.stringify(review.untracked) || snapshot.run.id !== run.id ||
          snapshot.run.session_id !== session || snapshot.run.status !== run.status || snapshot.run.metadata?.project_id !== project ||
          snapshot.run.metadata?.workspace_snapshot !== review.workspace || !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0)
        throw new Error("运行、差异或项目命令已变化；请重新审阅工作区。");
      const accepted = await requestConfirmation({ title: "验证当前工作区？", confirmLabel: "创建验证运行",
        description: `将在 ${review.workspace} 执行：\n${command}\n命令可能修改文件或访问网络；可能还需工具权限审批。结果只描述本次当前工作区，不证明原运行已验收。` });
      if (!accepted || !current(ticket)) return;
      const request: RunVerifyProjectParams = { source_run_id: run.id, session_id: session, project_id: project,
        expected_service_id: review.service_id, expected_workspace: review.workspace, expected_workspace_identity: review.workspace_identity,
        expected_command: command, expected_cursor: snapshot.cursor, idempotency_key: crypto.randomUUID(), confirmed: true };
      localStorage.setItem(key, JSON.stringify(request)); setUncertain(true);
      const response = await typedIPC.verifyProjectRun(request);
      if (!current(ticket)) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current(ticket)) return;
      if (after.service_id !== review.service_id || response.service_id !== review.service_id || response.project_id !== project ||
          response.session_id !== session || response.run.session_id !== session || response.run.metadata?.source_run_id !== run.id ||
          !response.run.metadata?.manual_project_validation || localStorage.getItem(key) !== JSON.stringify(request))
        throw new Error("验证回执无法确认；请查看任务时间线。");
      localStorage.removeItem(key); setUncertain(false);
      setValidation({ id: response.run.id, status: response.run.status });
      setNotice("验证运行已接单；如需权限请在审批面板处理，随后读取结果。执行结束不等于验收。");
    } catch (error) { if (current(ticket)) { setUncertain(!!pending()); setNotice(error instanceof Error ? error.message : "验证结果未确认；请检查任务时间线。"); } }
    finally { inFlight.current = false; setBusy(false); }
  };
  const refresh = async () => {
    if (!validation || inFlight.current) return;
    inFlight.current = true; setBusy(true); const ticket = ++epoch.current;
    try {
      const [before, data] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRun(validation.id)]);
      const after = await typedIPC.getServiceIdentity();
      if (!current(ticket)) return;
      if (before.service_id !== review.service_id || after.service_id !== review.service_id || data.run.id !== validation.id ||
          data.run.session_id !== session || data.run.metadata?.source_run_id !== run.id ||
          data.run.metadata?.project_id !== project || !data.run.metadata?.manual_project_validation) throw new Error("验证运行归属已变化");
      setValidation({ id: data.run.id, status: data.run.status, evidence: data.result && "verification" in data.result ? data.result.verification : null });
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : "无法读取验证结果"); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const clear = async () => {
    if (!uncertain || inFlight.current) return;
    const ticket = ++epoch.current, saved = pending();
    const accepted = await requestConfirmation({ title: "清除待核对验证记录？", confirmLabel: "已核对，清除",
      description: "请先检查原会话的任务时间线和工作区，验证命令可能已经执行。清除只移除本地记录，不会撤销命令或停止运行。" });
    if (!accepted || !current(ticket) || pending() !== saved) return;
    try { localStorage.removeItem(key); setUncertain(false); setNotice("待核对记录已清除；未撤销任何操作。"); }
    catch { setNotice("无法清除本地记录。"); }
  };
  return <section className="space-y-1 rounded border border-line p-2 text-xs"><p>验证当前工作区：独立运行项目配置的命令，保留实际输出与状态；不改变原任务验收结论。</p>
    <Button type="button" size="sm" variant="secondary" disabled={busy || uncertain || ipc.isMock} onClick={() => void start()}>审阅并验证当前工作区</Button>
    {uncertain && <div><p role="status">上次提交的结果未确认；请先检查原会话的验证运行，切勿盲目重复。</p><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void clear()}>已核对，清除待确认记录</Button></div>}
    {validation && <div><p>验证运行 {validation.id} · {validation.status}</p><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void refresh()}>读取验证结果</Button>
      {validation.evidence != null && <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(validation.evidence, null, 2)}</pre>}</div>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
