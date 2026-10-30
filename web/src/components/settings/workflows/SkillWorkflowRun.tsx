import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../../ipc";
import { useSessionStore } from "../../../stores/sessionStore";
import { useChat } from "../../../stores/chat";
import { requestConfirmation } from "../../modals/ConfirmationDialog";
import type { AgentRun, WorkflowEntry } from "../../../types/ipc";

type Intent = {
  skill_id: string; session_id: string; request: string; budget_tokens: number;
  idempotency_key: string; expected_service_id: string; expected_project_id: string | null; step_index: number;
};
type Pending = { host: string; workflow: string; intent: Intent };

export function SkillWorkflowRun({ workflow, onClose }: { workflow: WorkflowEntry; onClose: () => void }): JSX.Element {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const session = useSessionStore((s) => s.sessions.find((s) => s.id === s.currentSessionId));
  const project = useSessionStore((s) => s.currentProjectId);
  const mode = useChat((s) => s.executionMode);
  const [service, setService] = useState<string | null>(null);
  const [request, setRequest] = useState("");
  const [budget, setBudget] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [index, setIndex] = useState(0);
  const [chain, setChain] = useState<{ index: number; skill_id: string; run: AgentRun | null; continuation_blocked_reason: string | null }[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ index: number; runId: string; text: string; warning: string | null; scope: string } | null>(null);
  const previewInput = useRef<HTMLTextAreaElement>(null);
  const storageKey = `minimax:workflow-intent:${encodeURIComponent(workflow.id)}`;
  const supported = workflow.steps.length > 0 && workflow.steps.every((s) => s.type === "action" && s.action_type === "run-skill");
  const step = supported ? workflow.steps[index] : null;
  const skillId = typeof step?.config?.skill_id === "string" ? step.config.skill_id : "";
  const projectId = session?.project_id ?? null;
  const matches = !!sessionId && !!session && session.engine === "native" && (project === projectId || (!projectId && project === "inbox"));
  const validBudget = /^[1-9]\d*$/.test(budget) && Number.isSafeInteger(Number(budget));
  const viewScope = JSON.stringify([ipc.agentBaseUrl, service, sessionId, projectId, project, mode, workflow.id, workflow.steps, key, index]);
  const currentViewScope = useRef(viewScope);
  currentViewScope.current = viewScope;

  useEffect(() => { setPreview(null); setChain([]); }, [viewScope]);

  useEffect(() => {
    let live = true;
    const host = ipc.agentBaseUrl;
    setService(null);
    setRun(null);
    void typedIPC.getServiceIdentity().then((v) => { if (live && host === ipc.agentBaseUrl) setService(v.service_id); })
      .catch((e) => { if (live) setMessage(`服务身份不可用：${String(e)}`); });
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) {
        const value = JSON.parse(saved) as Pending;
        setPending(value); setKey(value.intent.idempotency_key); setIndex(value.intent.step_index ?? 0);
      } else {
        const savedKey = sessionStorage.getItem(`${storageKey}:key`);
        if (savedKey) setKey(savedKey);
      }
    } catch { setMessage("无法读取待确认请求，请先查看运行记录。"); }
    return () => { live = false; };
  }, [storageKey, sessionId, projectId]);
  useEffect(() => ipc.onSideCar(() => { setService(null); setRun(null); setPreview(null); setChain([]); setMessage("服务连接已变化，请关闭并重新打开运行入口，先检查原运行记录。"); }), []);

  const sameScope = (value: Pending) => value.workflow === workflow.id && value.host === ipc.agentBaseUrl &&
    value.intent.session_id === sessionId && value.intent.expected_service_id === service &&
    value.intent.expected_project_id === projectId && value.intent.skill_id === skillId && (value.intent.step_index ?? 0) === index && matches;
  const verifyScope = async (value: Pending) => {
    if (!sameScope(value) || mode !== "execute") throw new Error("会话、项目、服务、技能或执行模式已变化；请先检查原运行记录");
    const actual = await typedIPC.getServiceIdentity();
    const current = useSessionStore.getState();
    if (actual.service_id !== value.intent.expected_service_id || ipc.agentBaseUrl !== value.host || useChat.getState().executionMode !== "execute" ||
      current.currentSessionId !== value.intent.session_id ||
      current.sessions.find((s) => s.id === value.intent.session_id)?.engine !== "native" ||
      (current.sessions.find((s) => s.id === value.intent.session_id)?.project_id ?? null) !== value.intent.expected_project_id ||
      !(current.currentProjectId === value.intent.expected_project_id || (!value.intent.expected_project_id && current.currentProjectId === "inbox"))) {
      throw new Error("运行绑定已变化；请先检查原运行记录");
    }
  };
  const submit = async (retry?: Pending) => {
    if (busy || !service || !sessionId || !matches || mode !== "execute") return;
    if (!retry && (pending || !skillId.trim() || !request.trim() || !validBudget || !key.trim() || key.length > 128)) return;
    const value: Pending = retry ?? { host: ipc.agentBaseUrl, workflow: workflow.id, intent: {
      skill_id: skillId, session_id: sessionId, request: request.trim(), budget_tokens: Number(budget),
      idempotency_key: key, expected_service_id: service, expected_project_id: projectId, step_index: index,
    } };
    setBusy(true); setMessage("");
    try {
      await verifyScope(value);
      sessionStorage.setItem(`${storageKey}:key`, value.intent.idempotency_key);
      sessionStorage.setItem(storageKey, JSON.stringify(value));
      setPending(value);
      const result = await typedIPC.triggerWorkflow(workflow.id, undefined, value.intent);
      await verifyScope(value);
      const admission = result.skill_admission;
      if (!admission || admission.run.session_id !== value.intent.session_id) throw new Error("接单回执未确认，请检查运行记录");
      sessionStorage.removeItem(storageKey); setPending(null); setRun(admission.run);
      setRequest("");
      setMessage(`${admission.created ? "已接单" : "已找到原接单"}：${admission.run.id} · ${admission.run.status}。接单不代表完成。`);
    } catch (e) { setMessage(`提交被拒绝或接单结果未确认：${String(e)}。请检查步骤状态和运行记录；如需重试，请使用相同请求，避免重复执行。`); }
    finally { setBusy(false); }
  };
  const refreshChain = async () => {
    if (!service || !sessionId || !matches || !key.trim() || busy) return;
    const host = ipc.agentBaseUrl;
    setBusy(true);
    try {
      const result = await typedIPC.getWorkflowSkillChain(workflow.id, { idempotency_key: key, session_id: sessionId, expected_service_id: service, expected_project_id: projectId });
      const identity = await typedIPC.getServiceIdentity();
      const current = useSessionStore.getState();
      if (identity.service_id !== service || host !== ipc.agentBaseUrl || current.currentSessionId !== sessionId ||
        (current.sessions.find((s) => s.id === sessionId)?.project_id ?? null) !== projectId ||
        !(current.currentProjectId === projectId || (!projectId && current.currentProjectId === "inbox"))) throw new Error("运行绑定已变化");
      setChain(result.skill_chain);
      sessionStorage.setItem(`${storageKey}:key`, key);
      setMessage(result.definition_matches ? "已读取持久步骤状态。completed 只表示运行结束，请检查结果后再继续。" : "原链步骤与当前工作流不一致，不能继续；请查看原运行记录。");
    } catch (e) { setMessage(`无法读取步骤状态：${String(e)}`); }
    finally { setBusy(false); }
  };
  const viewResult = async (sourceIndex: number) => {
    if (!service || !sessionId || !matches || !key.trim() || busy || sourceIndex >= index) return;
    const scope = viewScope, host = ipc.agentBaseUrl;
    const chainScope = { idempotency_key: key, session_id: sessionId, expected_service_id: service, expected_project_id: projectId };
    const assertScope = async () => {
      const identity = await typedIPC.getServiceIdentity();
      const current = useSessionStore.getState();
      if (currentViewScope.current !== scope || host !== ipc.agentBaseUrl || identity.service_id !== service ||
          current.currentSessionId !== sessionId || current.sessions.find((s) => s.id === sessionId)?.engine !== "native" ||
          (current.sessions.find((s) => s.id === sessionId)?.project_id ?? null) !== projectId ||
          !(current.currentProjectId === projectId || (!projectId && current.currentProjectId === "inbox"))) {
        throw new Error("链、服务、会话或项目已变化，请重新查询");
      }
    };
    setBusy(true); setPreview(null);
    try {
      await assertScope();
      const before = await typedIPC.getWorkflowSkillChain(workflow.id, chainScope);
      const source = before.skill_chain.find((item) => item.index === sourceIndex);
      if (!source?.run) throw new Error("该步骤尚无持久运行");
      const detail = await typedIPC.getRun(source.run.id);
      const after = await typedIPC.getWorkflowSkillChain(workflow.id, chainScope);
      await assertScope();
      const confirmed = after.skill_chain.find((item) => item.index === sourceIndex);
      const profile = detail.run.metadata?.skill_execution as { skill_id?: string; project_id?: string | null } | undefined;
      if (!before.definition_matches || !after.definition_matches || !confirmed?.run || confirmed.run.id !== source.run.id || detail.run.id !== source.run.id ||
          detail.run.session_id !== sessionId || confirmed.run.session_id !== sessionId ||
          confirmed.skill_id !== source.skill_id || profile?.skill_id !== source.skill_id || profile.project_id !== projectId) {
        throw new Error("步骤运行归属无法确认，请重新查询");
      }
      const receipt = detail.result as unknown as Record<string, unknown> | null;
      if (!receipt || typeof receipt.text !== "string" || receipt.run_id !== detail.run.id || receipt.session_id !== sessionId || receipt.skill_id !== source.skill_id) {
        throw new Error("持久文本结果不可用或归属未知，请查看原运行记录");
      }
      const warning = confirmed.continuation_blocked_reason || (detail.run.status !== "completed" ? `运行状态为 ${detail.run.status}` : null);
      setChain(after.skill_chain);
      setPreview({ index: sourceIndex, runId: detail.run.id, text: receipt.text, warning, scope });
      setMessage("已读取持久文本。请核对内容；运行完成不代表结果已验收。");
    } catch (e) { setMessage(`无法确认步骤结果：${String(e)}`); }
    finally { setBusy(false); }
  };
  const insertResult = (all: boolean) => {
    if (!preview || preview.warning || preview.scope !== currentViewScope.current || busy || pending) return;
    const field = previewInput.current;
    const selected = all ? preview.text : field?.value.slice(field.selectionStart, field.selectionEnd) ?? "";
    if (!selected) { setMessage("请先在结果中选择文本，或使用插入全文。"); return; }
    setRequest((old) => `${old}${old ? "\n\n" : ""}[工作流步骤 ${preview.index} 的持久文本，运行 ${preview.runId}]\n${selected}`);
    setMessage("文本已加入可编辑请求；请检查后自行决定是否提交。");
  };
  const refreshRun = async () => {
    if (!run || !service || !sessionId) return;
    setBusy(true);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (identity.service_id !== service) throw new Error("服务身份已变化");
      const value = await typedIPC.getRun(run.id);
      const after = await typedIPC.getServiceIdentity();
      if (after.service_id !== service || value.run.session_id !== sessionId || ipc.agentBaseUrl !== valueHost) throw new Error("运行绑定已变化");
      setRun(value.run);
      setMessage(`运行 ${value.run.id} · ${value.run.status}${value.run.error ? ` · ${value.run.error}` : ""}。请在原会话查看详细结果。`);
    } catch (e) { setMessage(`无法读取运行状态：${String(e)}`); }
    finally { setBusy(false); }
  };
  const valueHost = ipc.agentBaseUrl;
  return <section className="space-y-2 border-t border-line pt-2 text-xs" aria-label="运行工作流技能">
    <div className="flex justify-between"><strong>运行工作流技能</strong><button type="button" onClick={onClose}>关闭</button></div>
    {!supported ? <p>仅支持线性技能步骤；混合步骤不会执行。</p> : <>
      <p>当前会话：{sessionId ?? "未选择"}。每步需手动提交；仅前面所有步骤均已完成时可继续。</p>
      {!matches || mode !== "execute" || !service ? <p>请选择所属项目中的 Native 会话、切换到执行模式，并确认服务可用。</p> : <>
        <label className="block">步骤<select value={index} disabled={busy || !!pending} onChange={(e) => { setIndex(Number(e.target.value)); setRequest(""); }}>
          {workflow.steps.map((s, i) => <option key={i} value={i}>#{i} · {String(s.config?.skill_id ?? "未指定")}</option>)}
        </select></label>
        <label className="block">请求<textarea className="w-full rounded border border-line bg-surface-0 p-2" value={request} disabled={busy || !!pending} onChange={(e) => setRequest(e.target.value)} /></label>
        <label className="block">Token 上限<input className="ml-2 rounded border border-line bg-surface-0 p-1" inputMode="numeric" value={budget} disabled={busy || !!pending} onChange={(e) => setBudget(e.target.value)} /></label>
        <label className="block">整条链的触发幂等键<input className="ml-2 rounded border border-line bg-surface-0 p-1" value={key} disabled={busy || !!pending} maxLength={128} onChange={(e) => { setKey(e.target.value); setChain([]); }} /></label>
        <button type="button" disabled={busy || !key.trim()} onClick={() => void refreshChain()}>查询链步骤状态</button>
        {chain.length > 0 && <ul>{chain.map((item) => <li key={item.index}>#{item.index} · {item.skill_id} · {item.run ? `${item.run.status} (${item.run.id})` : "尚未接单"}{item.continuation_blocked_reason ? ` · 不可继续：${item.continuation_blocked_reason}` : ""}{item.run && item.index < index && <> <button type="button" disabled={busy} onClick={() => void viewResult(item.index)}>查看持久文本</button></>}</li>)}</ul>}
        {preview && <div className="space-y-1 rounded border border-line p-2"><p>步骤 #{preview.index} · 运行 {preview.runId}{preview.warning ? ` · 不可继续：${preview.warning}` : ""}</p>
          <textarea ref={previewInput} aria-label="前序步骤持久文本" className="w-full rounded border border-line bg-surface-0 p-2" rows={6} readOnly value={preview.text} />
          {preview.warning ? <p>结果可能不完整或需要核对，不能附带到下一步。</p> : <><p>选择文本后插入，或插入全文；插入后仍可编辑，不会自动提交。</p>
            <button type="button" disabled={busy || !!pending} onClick={() => insertResult(false)}>插入选中文本</button>{" "}
            <button type="button" disabled={busy || !!pending || !preview.text} onClick={() => insertResult(true)}>插入全文</button></>}
        </div>}
        <button type="button" disabled={busy || !!pending || !skillId.trim() || !request.trim() || !validBudget || !key.trim()} onClick={() => void submit()}>提交运行</button>
        {pending && <div><p>存在未确认请求：{pending.intent.idempotency_key}。同一请求重试使用原键，不会自动重放。</p>
          <button type="button" disabled={busy || !sameScope(pending)} onClick={() => void submit(pending)}>重试同一请求</button>{" "}
          <button type="button" disabled={busy} onClick={() => void (async () => {
            const ok = await requestConfirmation({ title: "清除待确认请求", description: "这不会取消可能已接单的运行。请先检查原会话的运行记录，避免重复执行。", confirmLabel: "清除本地记录" });
            if (ok) { sessionStorage.removeItem(storageKey); setPending(null); }
          })()}>清除本地记录</button></div>}
        {run && <button type="button" disabled={busy} onClick={() => void refreshRun()}>刷新运行状态</button>}
      </>}
    </>}
    {message && <p role="status" className="break-words">{message}</p>}
  </section>;
}
