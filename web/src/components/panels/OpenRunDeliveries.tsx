import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import { Button } from "../../ui";

export function OpenRunDeliveries({ host, service, project, session, run, schedule }: {
  host: string; service: string; project: string; session: string; run: string;
  schedule?: { job: string; occurrence: string; revision: number; workspace: string };
}): JSX.Element {
  const originSession = useSessionStore((state) => state.currentSessionId);
  const originProject = useSessionStore((state) => state.currentProjectId);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setBusy(false); setError(""); return () => { ++epoch.current; }; }, [host, service, project, session, run, originSession, originProject, schedule?.job, schedule?.occurrence, schedule?.revision, schedule?.workspace]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setBusy(false); setError("连接已变化，请重新选择任务。"); }), []);
  useEffect(() => useSessionStore.subscribe((state) => {
    if (state.currentProjectId !== originProject || state.currentSessionId !== originSession || ipc.agentBaseUrl !== host) {
      ++epoch.current; setBusy(false);
    }
  }), [host, originProject, originSession]);
  const open = async () => {
    if (lock.current) return;
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentProjectId === originProject && useSessionStore.getState().currentSessionId === originSession;
    lock.current = true; setBusy(true); setError("");
    try {
      const [identity, snapshot, owner, projects, summary] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getRunSteps(run), typedIPC.getSession(session), typedIPC.listProjects(),
        typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: service, run_id: run }),
      ]);
      const [after, ownerAfter, latest] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getRunSteps(run)]);
      if (!current()) return;
      const selectedProject = projects.projects.find((item) => item.id === project && !item.archived);
      const matchesRun = (value: typeof snapshot) => {
        const recorded = value.run.metadata?.schedule as { job_id?: string; occurrence_id?: string; revision?: number } | undefined;
        return value.run.id === run && value.run.session_id === session && value.run.metadata?.project_id === project &&
          Number.isSafeInteger(value.cursor) && value.cursor >= 0 &&
          value.steps.every((step) => step.run_id === run && step.session_id === session) &&
          (!schedule || (recorded?.job_id === schedule.job && recorded.occurrence_id === schedule.occurrence &&
            recorded.revision === schedule.revision && value.run.metadata?.workspace_snapshot === schedule.workspace &&
            value.run.metadata?.model_snapshot?.engine === "native"));
      };
      if (!service || identity.service_id !== service || after.service_id !== service || !selectedProject ||
          owner.session.id !== session || owner.session.project_id !== project || owner.session.archived ||
          ownerAfter.session.id !== session || ownerAfter.session.project_id !== project || ownerAfter.session.archived ||
          summary.project_id !== project || summary.service_id !== service || summary.detail?.id !== run || summary.detail.session_id !== session ||
          !matchesRun(snapshot) || !matchesRun(latest))
        throw new Error("服务、项目、会话或运行关联已变化，请重新选择任务。");
      const question = useChat.getState().askUser;
      if ((originSession !== session || originProject !== project) && question?.questions.some((item) => item.isSecret))
        throw new Error("当前有未完成的保密问题，请先处理，避免丢失未保存输入。");
      const detail: { host: string; service: string; project: string; session: string; run: string;
        originProject: string | null; originSession: string | null; verifiedSession: typeof ownerAfter.session;
        verifiedProject: typeof selectedProject; result?: string } = {
        host, service, project, session, run, originProject, originSession, verifiedSession: ownerAfter.session, verifiedProject: selectedProject,
      };
      window.dispatchEvent(new CustomEvent("minimax:open-verified-run-deliveries", { detail }));
      if (detail.result !== "opened" && current()) setError(detail.result || "无法打开交付物工作区，请重试。");
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  return <div className="mt-2 space-y-1 text-xs"><Button type="button" size="sm" variant="secondary" disabled={busy} loading={busy} onClick={() => void open()}>在交付物工作区查看此任务</Button>
    <p>可在同一处查看明确关联的附件和文本成果；关联不证明由该任务生成。</p>
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
  </div>;
}
