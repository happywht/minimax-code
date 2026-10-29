import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { useChat } from "../../stores/chat";
import type { AgentRun, SkillInfo, SkillInvokeArgs } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";

type Intent = { skillId: string; args: SkillInvokeArgs };
const active = new Set(["queued", "planning", "running", "awaiting_approval", "waiting_input"]);

export function SkillRunPanel({ skill, onClose }: { skill: SkillInfo; onClose: () => void }): JSX.Element {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const projectId = session?.project_id ?? null;
  const executionMode = useChat((s) => s.executionMode);
  const [service, setService] = useState<string | null>(null);
  const [request, setRequest] = useState("");
  const [budget, setBudget] = useState("");
  const [pending, setPending] = useState<Intent | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selected, setSelected] = useState<{ run: AgentRun; result: Record<string, unknown> | null } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const storageKey = service && sessionId ? `minimax:skill-intent:${encodeURIComponent(service)}:${encodeURIComponent(sessionId)}:${encodeURIComponent(projectId ?? "")}` : null;
  const budgetValid = !budget || (/^[1-9]\d*$/.test(budget) && Number.isSafeInteger(Number(budget)));

  useEffect(() => {
    const current = ++epoch.current, host = ipc.agentBaseUrl;
    setService(null); setPending(null); setRuns([]); setSelected(null); setMessage(""); setRequest(""); setBudget(""); setBusy(false);
    if (sessionId && session && session.engine !== "codex") {
      void typedIPC.getServiceIdentity().then((value) => {
        if (current === epoch.current && host === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === sessionId) setService(value.service_id);
      }).catch((error) => { if (current === epoch.current) setMessage(`服务身份不可用：${String(error)}`); });
    }
    return () => { ++epoch.current; };
  }, [sessionId, projectId, session?.engine, skill.id]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setService(null); setPending(null); setRuns([]); setSelected(null); setRequest(""); setMessage("连接已变化，请重新打开技能运行入口。"); }), []);
  useEffect(() => {
    if (!storageKey || !service || !sessionId) return;
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) {
        const value = JSON.parse(saved) as Intent;
        if (value.args?.session_id === sessionId && value.args.expected_service_id === service && value.args.expected_project_id === projectId) setPending(value);
      }
    } catch { setMessage("无法读取待确认请求，请先查看运行记录。"); }
  }, [storageKey, service, sessionId, projectId]);

  const valid = (ticket: number, host: string) => ticket === epoch.current && host === ipc.agentBaseUrl &&
    useSessionStore.getState().currentSessionId === sessionId &&
    (useSessionStore.getState().currentProjectId === projectId || (!projectId && useSessionStore.getState().currentProjectId === "inbox")) &&
    (useSessionStore.getState().sessions.find((item) => item.id === sessionId)?.project_id ?? null) === projectId;
  const identity = async () => {
    const actual = await typedIPC.getServiceIdentity();
    if (!service || actual.service_id !== service) throw new Error("服务身份已变化，请重新打开入口");
  };
  const refresh = async () => {
    if (!sessionId || !service) return;
    const ticket = epoch.current, host = ipc.agentBaseUrl;
    setBusy(true);
    try {
      await identity();
      const result = await typedIPC.listRuns({ session_id: sessionId, limit: 100 });
      await identity();
      if (!valid(ticket, host)) return;
      const items = result.runs.filter((run) => run.session_id === sessionId &&
        (run.metadata?.skill_execution as { skill_id?: string; project_id?: string | null } | undefined)?.skill_id === skill.id &&
        (run.metadata?.skill_execution as { project_id?: string | null } | undefined)?.project_id === projectId);
      setRuns(items);
      if (pending) {
        const found = items.find((run) => run.request_key === pending.args.idempotency_key && pending.skillId === skill.id);
        if (found && storageKey) { sessionStorage.removeItem(storageKey); setPending(null); setMessage(`已确认接单：${found.id}`); }
      }
    } catch (error) { if (valid(ticket, host)) setMessage(`无法读取运行记录：${String(error)}`); }
    finally { if (valid(ticket, host)) setBusy(false); }
  };
  useEffect(() => { if (service && sessionId) void refresh(); }, [service, sessionId, skill.id]);

  const submit = async (intent?: Intent) => {
    if (!sessionId || !session || !service || !storageKey || busy || !skill.enabled || session.engine === "codex" || executionMode !== "execute") return;
    if (!intent && (!request.trim() || !budgetValid || pending)) return;
    const next = intent ?? { skillId: skill.id, args: { session_id: sessionId, request: request.trim(),
      idempotency_key: `skill:${crypto.randomUUID()}`, expected_service_id: service, expected_project_id: projectId,
      ...(budget ? { budget_tokens: Number(budget) } : {}) } };
    if (next.skillId !== skill.id || next.args.session_id !== sessionId || next.args.expected_service_id !== service || next.args.expected_project_id !== projectId) return;
    const ticket = epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setMessage("");
    try {
      await identity();
      // Record intent before sending so a lost acknowledgement can reuse the same key.
      sessionStorage.setItem(storageKey, JSON.stringify(next));
      setPending(next);
      const result = await typedIPC.admitSkill(next.skillId, next.args);
      await identity();
      if (!valid(ticket, host)) return;
      if (result.run.session_id !== sessionId) throw new Error("接单会话不匹配，请查看运行记录");
      sessionStorage.removeItem(storageKey); setPending(null); setRequest("");
      setRuns((old) => [result.run, ...old.filter((run) => run.id !== result.run.id)]);
      setMessage(`已接单：${result.run.id} · ${result.run.status}。接单不代表完成。`);
    } catch (error) { if (valid(ticket, host)) setMessage(`接单结果未确认：${String(error)}。请刷新记录或用相同请求重试，避免重复执行。`); }
    finally { if (valid(ticket, host)) setBusy(false); }
  };
  const open = async (runId: string, cancel = false) => {
    if (!sessionId || !service || busy) return;
    const ticket = epoch.current, host = ipc.agentBaseUrl;
    setBusy(true);
    try {
      await identity();
      if (cancel) await typedIPC.cancelRun(runId);
      const data = await typedIPC.getRun(runId);
      await identity();
      if (!valid(ticket, host)) return;
      const profile = data.run.metadata?.skill_execution as { skill_id?: string; project_id?: string | null } | undefined;
      if (data.run.session_id !== sessionId || profile?.skill_id !== skill.id || profile.project_id !== projectId) throw new Error("运行归属已变化");
      setSelected({ run: data.run, result: data.result as unknown as Record<string, unknown> | null });
      setRuns((old) => old.map((run) => run.id === runId ? data.run : run));
      if (cancel) setMessage("已请求停止；最终状态需刷新确认。");
    } catch (error) { if (valid(ticket, host)) setMessage(String(error)); }
    finally { if (valid(ticket, host)) setBusy(false); }
  };
  return <section className="space-y-3 rounded-lg border border-line bg-surface-1 p-3 text-xs" aria-label={`运行技能 ${skill.name}`}>
    <div className="flex justify-between gap-2"><strong>运行 {skill.name}</strong><button type="button" onClick={onClose}>关闭</button></div>
    <p>绑定当前 Native 会话及其项目、工作区和服务；权限、预算和同会话队列由服务端执行。</p>
    {executionMode === "plan" && <p>技能入口为执行模式；请先切换到执行模式再提交或重试。</p>}
    {!sessionId || !session || session.engine === "codex" ? <p>请先选择 Native 会话。</p> : !service ? <p>正在确认服务身份或服务不可用。</p> : <>
      <label className="block">任务要求<textarea className="mt-1 w-full rounded border border-line bg-surface-0 p-2" rows={3} value={request} disabled={busy || !!pending} onChange={(e) => setRequest(e.target.value)} /></label>
      <label className="block">Token 上限（可选，留空使用服务默认）<input className="ml-2 rounded border border-line bg-surface-0 p-1" inputMode="numeric" value={budget} disabled={busy || !!pending} onChange={(e) => setBudget(e.target.value)} /></label>
      {!budgetValid && <p>请输入有效正整数。</p>}
      <button type="button" disabled={busy || !!pending || !request.trim() || !budgetValid || !skill.enabled || executionMode !== "execute"} onClick={() => void submit()} className="rounded border border-line px-2 py-1">提交技能任务</button>
      {pending && <div><p>有接单结果未确认的请求（{pending.skillId}）；请先刷新记录或用同一请求重试。</p>
        {pending.skillId === skill.id && <button type="button" disabled={busy || executionMode !== "execute"} onClick={() => void submit(pending)}>重试同一请求</button>}{" "}
        <button type="button" disabled={busy} onClick={() => { void (async () => {
          const ticket = epoch.current, host = ipc.agentBaseUrl;
          const accepted = await requestConfirmation({ title: "清除本地待确认请求", description: "这只清除本页保存的重试信息，不会取消服务中可能已接单的运行。请先检查任务记录，避免重复执行。", confirmLabel: "清除重试信息" });
          if (accepted && storageKey && valid(ticket, host)) { sessionStorage.removeItem(storageKey); setPending(null); }
        })(); }}>清除本地重试信息</button></div>}
      <div className="flex items-center gap-2"><strong>当前会话的技能运行</strong><button type="button" disabled={busy} onClick={() => void refresh()}>刷新</button></div>
      {runs.length === 0 && <p>没有已读取的运行记录。</p>}
      <ul className="space-y-1">{runs.map((run) => <li key={run.id} className="flex flex-wrap gap-2"><span>{run.id} · {run.status}</span>
        <button type="button" disabled={busy} onClick={() => void open(run.id)}>查看结果</button>
        {active.has(run.status) && <button type="button" disabled={busy} onClick={() => void open(run.id, true)}>请求停止</button>}</li>)}</ul>
      {selected && <div className="rounded border border-line p-2"><p>{selected.run.id} · {selected.run.status}</p>
        {selected.result ? <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(selected.result, null, 2)}</pre> : <p>尚无持久结果；状态或输出可能仍未知。</p>}
        <p>运行完成不代表验收；请核对结果、截断和验证证据。</p></div>}
    </>}
    {message && <p role="status" className="break-words">{message}</p>}
  </section>;
}
