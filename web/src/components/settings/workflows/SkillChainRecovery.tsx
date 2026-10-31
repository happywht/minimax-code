import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../../ipc";
import { useSessionStore } from "../../../stores/sessionStore";
import { requestConfirmation } from "../../modals/ConfirmationDialog";
import { SkillWorkflowRun } from "./SkillWorkflowRun";

type Page = Awaited<ReturnType<typeof typedIPC.listWorkflowSkillChains>>;
type Chain = Page["chains"][number];
type Selection = { chain: Chain; host: string; service: string; session: string; project: string | null; index: number };

export function SkillChainRecovery(): JSX.Element {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const session = useSessionStore((s) => s.sessions.find((session) => session.id === s.currentSessionId));
  const project = useSessionStore((s) => s.currentProjectId);
  const projectId = session?.project_id ?? null;
  const matches = !!sessionId && !!session && session.engine === "native" && (project === projectId || (!projectId && project === "inbox"));
  const [service, setService] = useState<string | null>(null);
  const [chains, setChains] = useState<Chain[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);

  useEffect(() => {
    const generation = ++epoch.current, host = ipc.agentBaseUrl;
    setService(null); setChains([]); setSelection(null); setNext(null); setBusy(false); setMessage("");
    if (matches) void typedIPC.getServiceIdentity().then((v) => {
      if (generation === epoch.current && host === ipc.agentBaseUrl) setService(v.service_id);
    }).catch((e) => { if (generation === epoch.current) setMessage(`服务身份不可用：${String(e)}`); });
    return () => { ++epoch.current; };
  }, [sessionId, projectId, project, matches]);
  useEffect(() => ipc.onSideCar(() => {
    ++epoch.current; setService(null); setChains([]); setSelection(null); setNext(null); setBusy(false);
    setMessage("服务连接已变化，请重新打开恢复入口。");
  }), []);

  const scopeValid = async (generation: number, host: string, expectedService: string, expectedSession: string, expectedProject: string | null) => {
    const identity = await typedIPC.getServiceIdentity();
    const current = useSessionStore.getState();
    if (generation !== epoch.current || host !== ipc.agentBaseUrl || identity.service_id !== expectedService ||
        current.currentSessionId !== expectedSession || current.sessions.find((s) => s.id === expectedSession)?.engine !== "native" ||
        (current.sessions.find((s) => s.id === expectedSession)?.project_id ?? null) !== expectedProject ||
        !(current.currentProjectId === expectedProject || (!expectedProject && current.currentProjectId === "inbox"))) throw new Error("服务、会话或项目已变化");
  };
  const load = async (offset: number) => {
    if (!service || !sessionId || !matches || busy) return;
    const generation = epoch.current, host = ipc.agentBaseUrl;
    setBusy(true);
    try {
      await scopeValid(generation, host, service, sessionId, projectId);
      const result = await typedIPC.listWorkflowSkillChains({ session_id: sessionId, expected_service_id: service, expected_project_id: projectId, offset });
      await scopeValid(generation, host, service, sessionId, projectId);
      setChains((old) => offset ? [...old, ...result.chains] : result.chains);
      setNext(result.next_offset);
      setMessage(result.chains.length ? "已读取持久链；运行完成不代表验收。" : "此会话没有更多可列出的链。早于链账本的运行请在原会话查看。");
    } catch (e) { if (generation === epoch.current) setMessage(`无法读取链：${String(e)}`); }
    finally { if (generation === epoch.current) setBusy(false); }
  };
  const select = async (chain: Chain) => {
    if (!chain.workflow || !chain.definition_matches || !service || !sessionId || busy) return;
    const generation = epoch.current, host = ipc.agentBaseUrl;
    setBusy(true);
    try {
      if (selection) {
        const ok = await requestConfirmation({ title: "切换恢复的链", description: "切换会关闭当前输入面板；浏览器存储可用时，草稿和待确认请求保存在当前浏览器会话中。", confirmLabel: "切换" });
        if (!ok) return;
      }
      await scopeValid(generation, host, service, sessionId, projectId);
      const status = await typedIPC.getWorkflowSkillChain(chain.workflow_id, { idempotency_key: chain.trigger_key, session_id: sessionId, expected_service_id: service, expected_project_id: projectId });
      await scopeValid(generation, host, service, sessionId, projectId);
      if (!status.definition_matches) throw new Error("工作流定义已变化，原链只能查看");
      const firstMissing = status.skill_chain.find((step) => !step.run);
      const chosen = firstMissing?.index ?? Math.max(0, status.skill_chain.length - 1);
      setSelection({ chain: { ...chain, steps: status.skill_chain }, host, service, session: sessionId, project: projectId, index: chosen });
    } catch (e) { if (generation === epoch.current) setMessage(`无法恢复链：${String(e)}`); }
    finally { if (generation === epoch.current) setBusy(false); }
  };

  return <section className="space-y-2 rounded border border-line p-3 text-xs" aria-label="恢复技能工作流链">
    <strong>恢复已开始的技能链</strong>
    <p>按当前服务、项目和 Native 会话查找；选择链只恢复操作上下文，不会执行任务。</p>
    {!matches || !service ? <p>请选择所属项目中的 Native 会话并确认服务可用。</p> : <>
      <button type="button" disabled={busy} onClick={() => void load(0)}>查询已开始的链</button>
      <ul className="space-y-2">{chains.map((chain) => <li key={`${chain.workflow_id}:${chain.trigger_key}`} className="rounded border border-line p-2">
        <p>{chain.workflow?.name ?? `已删除工作流 ${chain.workflow_id}`} · {chain.created_at}</p>
        {!chain.workflow ? <p>工作流已删除，只能查看历史，不能继续。</p> : !chain.definition_matches ? <p>工作流定义已变化，只能查看原链进度。</p> : null}
        <ul>{chain.steps.map((step) => <li key={step.index}>#{step.index} · {step.skill_id} · {step.run ? `${step.run.status} (${step.run.id})` : "尚未接单"}{step.continuation_blocked_reason ? ` · ${step.continuation_blocked_reason}` : ""}</li>)}</ul>
        {chain.workflow && chain.definition_matches && <button type="button" disabled={busy} onClick={() => void select(chain)}>选择并恢复此链</button>}
      </li>)}</ul>
      {next !== null && <button type="button" disabled={busy} onClick={() => void load(next)}>加载更多</button>}
      {selection?.chain.workflow && <SkillWorkflowRun key={`${selection.chain.workflow_id}:${selection.chain.trigger_key}`} workflow={selection.chain.workflow} recovery={{ key: selection.chain.trigger_key, index: selection.index, host: selection.host, service: selection.service, session: selection.session, project: selection.project }} onClose={() => void (async () => {
        const ok = await requestConfirmation({ title: "关闭恢复面板", description: "浏览器存储可用时，草稿和待确认请求保存在当前浏览器会话中。", confirmLabel: "关闭" });
        if (ok) setSelection(null);
      })()} />}
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
