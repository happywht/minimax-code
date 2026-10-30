import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun } from "../../types/ipc";
import { Button } from "../../ui";
import { RunTextArtifacts } from "./RunTextArtifacts";

/** Resolve the current service only when the user opens this task's text results. */
export function RunTimelineTextArtifacts({ run }: { run: AgentRun }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.currentProjectId);
  const sessionProject = useSessionStore((state) => state.sessions.find((item) => item.id === session)?.project_id);
  const host = ipc.agentBaseUrl;
  const [service, setService] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    epoch.current++; setService(null); setBusy(false); setError("");
    return () => { epoch.current++; };
  }, [host, project, session, sessionProject, run.id]);
  useEffect(() => ipc.onSideCar(() => {
    epoch.current++; setService(null); setBusy(false); setError("连接已变化，请重新打开文本成果。");
  }), []);
  if (!project || !session || session !== run.session_id || sessionProject !== project || run.metadata?.project_id !== project) return null;
  const open = async () => {
    const ticket = ++epoch.current, origin = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === origin &&
      useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project &&
      useSessionStore.getState().sessions.find((item) => item.id === session)?.project_id === project;
    setBusy(true); setError("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (!identity.service_id) throw new Error("无法确认服务身份");
      setService(identity.service_id);
    } catch (reason) { if (current()) setError(String(reason)); }
    finally { if (current()) setBusy(false); }
  };
  return <div className="text-xs">
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => service ? setService(null) : void open()}>
      {busy ? "正在确认服务…" : service ? "收起任务文本成果" : "查看任务文本成果"}
    </Button>
    {error && <p role="alert">{error}</p>}
    {service && <Button type="button" size="sm" variant="ghost" onClick={() => window.dispatchEvent(new CustomEvent("minimax:open-run-deliveries", { detail: { host, service, project, session, run: run.id } }))}>在交付物工作区查看此任务</Button>}
    {service && <RunTextArtifacts key={`${host}:${service}:${project}:${session}:${run.id}`} runId={run.id} sessionId={session} projectId={project} serviceId={service} />}
  </div>;
}
