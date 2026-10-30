import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import type { AgentRun } from "../../types/ipc";
import { Button } from "../../ui";

const stopped = new Set(["interrupted", "failed", "cancelled", "budget_paused"]);

/** Copy selected, unverified plan steps into an editable draft, without resuming the run. */
export function InterruptedPlanDraft({ run }: { run: AgentRun }): JSX.Element | null {
  const session = useSessionStore((state) => state.currentSessionId);
  const project = useSessionStore((state) => state.currentProjectId);
  const host = ipc.agentBaseUrl;
  const plan = run.metadata?.codex_plan;
  const signature = JSON.stringify(plan);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), lock = useRef(false);
  useEffect(() => { ++epoch.current; setSelected([]); setBusy(false); setNotice(""); return () => { ++epoch.current; }; }, [host, project, session, run.id, run.status, signature]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setBusy(false); setNotice("连接已变化，请刷新运行记录。"); }), []);
  if (!session || !project || run.session_id !== session || run.metadata?.project_id !== project ||
      run.metadata?.engine !== "codex" || !stopped.has(run.status) || !plan || plan.truncated) return null;
  const remaining = plan.steps.map((step, index) => ({ step, index })).filter(({ step }) => step.status !== "completed");
  if (!remaining.length) return null;
  const add = async () => {
    if (lock.current || !selected.length) return;
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project &&
      useSessionStore.getState().sessions.some((item) => item.id === session && item.project_id === project && !item.archived);
    lock.current = true; setBusy(true); setNotice("");
    try {
      const before = await typedIPC.getServiceIdentity();
      const [snapshot, owner] = await Promise.all([typedIPC.getRunSteps(run.id), typedIPC.getSession(session)]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const saved = snapshot.run;
      if (!before.service_id || before.service_id !== after.service_id || saved.id !== run.id || saved.session_id !== session ||
          saved.metadata?.project_id !== project || saved.metadata?.engine !== "codex" || saved.status !== run.status ||
          JSON.stringify(saved.metadata?.codex_plan) !== signature || owner.session.id !== session ||
          owner.session.project_id !== project || owner.session.archived || !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
          snapshot.steps.some((step) => step.run_id !== run.id || step.session_id !== session))
        throw new Error("服务、计划或运行归属已变化，请刷新后重新选择。");
      if (["sending", "streaming", "cancelling"].includes(useChat.getState().status)) throw new Error("当前会话仍在运行，请等待后再准备。");
      const lines = remaining.filter(({ index }) => selected.includes(index)).map(({ step, index }) =>
        `${index + 1}. ${step.step}（${step.status === "inProgress" ? "停止时记录为进行中" : "待处理"}）`);
      const detail: { host: string; project: string; session: string; text: string; result?: string } = {
        host, project, session,
        text: `请先核对原运行、当前工作区和外部副作用，再决定如何处理以下步骤；不要假定计划状态等于实际执行结果。\n\n来源运行：${run.id} · 状态：${run.status}\n${lines.join("\n")}\n\n已核对的进展与后续目标：（请填写）`,
      };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setSelected([]); setNotice("已加入草稿；请核对、编辑并按原恢复流程处理。"); }
      else setNotice(detail.result ?? "聊天输入区未就绪，请稍后重试。");
    } catch (error) { if (current()) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; if (current()) setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-line p-2">
    <p>选择计划中的未完成步骤，准备后续草稿。状态是模型记录，须核对实际结果。</p>
    {remaining.map(({ step, index }) => <label key={index} className="flex items-start gap-2 break-words">
      <input type="checkbox" checked={selected.includes(index)} disabled={busy} onChange={(event) => {
        const checked = event.target.checked;
        setSelected((value) => checked ? [...value, index] : value.filter((item) => item !== index));
      }} />
      <span>{index + 1}. {step.step} · {step.status === "inProgress" ? "进行中" : "待处理"}</span>
    </label>)}
    <Button type="button" size="sm" variant="secondary" disabled={busy || !selected.length} onClick={() => void add()}>将所选步骤加入草稿</Button>
    {notice && <p role="status" className="break-words">{notice}</p>}
  </div>;
}
