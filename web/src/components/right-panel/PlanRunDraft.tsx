import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import type { AgentRun } from "../../types/ipc";
import { Button } from "../../ui";

type Source = { service: string; cursor: number; plan: string };

/** Review a persisted plan and prepare a draft; this never starts execution. */
export function PlanRunDraft({ run }: { run: AgentRun }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.currentProjectId);
  const host = ipc.agentBaseUrl;
  const [source, setSource] = useState<Source | null>(null), [text, setText] = useState("");
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    ++epoch.current; setSource(null); setText(""); setBusy(false); setNotice("");
    return () => { ++epoch.current; };
  }, [run.id, session, project, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setSource(null); setText(""); setBusy(false); setNotice("连接已变化，请重新读取计划。"); }), []);
  if (!session || !project || run.session_id !== session || run.mode !== "plan" || run.status !== "completed") return null;
  const current = (ticket: number) => {
    const state = useSessionStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && state.currentSessionId === session &&
      state.currentProjectId === project && state.sessions.some((item) => item.id === session && item.project_id === project && !item.archived);
  };
  const read = async (ticket: number): Promise<Source> => {
    const identity = await typedIPC.getServiceIdentity();
    if (!current(ticket) || !identity.service_id) throw new Error("服务、会话或项目已变化。");
    const [snapshot, summary] = await Promise.all([
      typedIPC.getRunSteps(run.id),
      typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: identity.service_id, run_id: run.id }),
    ]);
    const after = await typedIPC.getServiceIdentity();
    const saved = snapshot.run, detail = summary.detail, structured = saved.metadata?.codex_plan;
    if (!current(ticket) || after.service_id !== identity.service_id || summary.service_id !== identity.service_id ||
        summary.project_id !== project || saved.id !== run.id || saved.session_id !== session ||
        saved.metadata?.project_id !== project || saved.mode !== "plan" || saved.status !== "completed" ||
        saved.metadata?.truncated || structured?.truncated || !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
        snapshot.steps.some((step) => step.run_id !== run.id || step.session_id !== session) ||
        !detail || detail.id !== run.id || detail.session_id !== session || detail.status !== "completed")
      throw new Error("计划运行或归属已变化，请重新读取。");
    if (detail.text_truncated) throw new Error("计划文本已截断，请在原会话核对后手动准备。");
    const sections = [detail.text?.trim() || "", structured ? [structured.explanation ?? "",
      ...structured.steps.map((step, index) => `${index + 1}. ${step.step} (${step.status})`)].filter(Boolean).join("\n") : ""].filter(Boolean);
    const plan = sections.join("\n\n");
    if (!plan || plan.length > 8000) throw new Error("没有完整且可用的计划文本，请查看原会话。");
    return { service: identity.service_id, cursor: snapshot.cursor, plan };
  };
  const load = async () => {
    if (busy) return;
    const ticket = ++epoch.current; setBusy(true); setSource(null); setText(""); setNotice("");
    try { const value = await read(ticket); if (current(ticket)) { setSource(value); setText(value.plan); } }
    catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const add = async () => {
    if (!source || busy || !text.trim()) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const fresh = await read(ticket);
      if (!current(ticket)) return;
      if (JSON.stringify(fresh) !== JSON.stringify(source)) throw new Error("原计划记录已变化；请重新读取并审阅。");
      if (["sending", "streaming", "cancelling"].includes(useChat.getState().status)) throw new Error("当前会话仍在运行，请等待后再准备。");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = {
        host, project, session, text: `请执行以下我审阅修改后的计划；执行前核对工作区和必要授权，遇到需要决定的事项请询问。\n\n来源规划运行：${run.id}\n\n${text}`,
      };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      if (detail.result === "已添加到当前会话草稿，尚未发送。") {
        useChat.getState().setExecutionMode("execute"); setSource(null); setText("");
        setNotice("计划已加入草稿并切换到执行模式；请检查内容和预算后自行发送。");
      } else setNotice(detail.result ?? "聊天输入区未就绪，请稍后重试。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-line p-2 text-xs">
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void load()}>读取历史计划并编辑</Button>
    {source && <><p>原计划是已保存的模型输出，不代表执行或验收。编辑后只会加入草稿。</p>
      <textarea aria-label="编辑历史计划" className="block w-full rounded border border-line bg-surface-1 p-2" rows={7} maxLength={8000} disabled={busy} value={text} onChange={(event) => setText(event.target.value)} />
      <Button type="button" size="sm" variant="secondary" disabled={busy || !text.trim()} onClick={() => void add()}>将修改后的计划加入草稿</Button></>}
    {notice && <p role="status" className="break-words text-status-warning">{notice}</p>}
  </div>;
}
