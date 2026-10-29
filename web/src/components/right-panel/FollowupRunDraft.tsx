import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, VerificationEvidence } from "../../types/ipc";
import { Button } from "../../ui";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const statuses: Record<string, string> = { completed: "运行结束", failed: "失败", cancelled: "已取消", interrupted: "已中断" };
const verdicts: Record<string, string> = { passed: "配置的验证通过", failed: "验证未通过", unknown: "未知", not_applicable: "不适用" };
const clip = (text: string, limit: number) => text.length > limit ? `${text.slice(0, limit)}…（已截断）` : text;

type Prepared = { text: string; service: string };

/** Prepares editable context from a persisted run; it never sends a task. */
export function FollowupRunDraft({ run }: { run: AgentRun }): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.currentProjectId);
  const host = ipc.agentBaseUrl;
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setPrepared(null); setMessage(""); setBusy(false); return () => { ++epoch.current; }; }, [run.id, session, project, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setPrepared(null); setBusy(false); setMessage("连接变化，请重新准备草稿。"); }), []);
  const available = !!project && !!session && run.session_id === session && run.metadata?.project_id === project && terminal.has(run.status);
  if (!available || !project || !session) return null;
  const isCurrent = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const read = async (ticket: number): Promise<Prepared> => {
    const identity = await typedIPC.getServiceIdentity();
    if (!isCurrent(ticket) || !identity.service_id) throw new Error("服务或会话已变化");
    const [snapshot, attachments, summary] = await Promise.all([
      typedIPC.getRunSteps(run.id), typedIPC.listAttachments(project),
      typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: identity.service_id, run_id: run.id })
        .then((value) => ({ value }), (error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          if (/Project or service identity changed|Run project association changed/i.test(message)) throw error;
          return { value: null };
        }),
    ]);
    const after = await typedIPC.getServiceIdentity();
    if (!isCurrent(ticket) || after.service_id !== identity.service_id || attachments.service_id !== identity.service_id ||
        snapshot.run.id !== run.id || snapshot.run.session_id !== session || snapshot.run.metadata?.project_id !== project || !terminal.has(snapshot.run.status) ||
        (summary.value !== null && (summary.value.project_id !== project || summary.value.service_id !== identity.service_id || !summary.value.detail ||
        summary.value.detail.id !== run.id || summary.value.detail.session_id !== session || summary.value.detail.status !== snapshot.run.status)))
      throw new Error("运行、项目或服务归属已变化，请刷新");
    const saved = snapshot.run, raw = saved.metadata?.verification as VerificationEvidence | undefined;
    const evidence = raw?.version === 1 ? raw : undefined;
    const result = evidence?.validation;
    const lines = ["请基于以下已记录运行准备后续任务（目标请补充；请先核对原记录与文件）：",
      `运行：${saved.id} · ${clip(saved.title || "未命名", 240)}`, `运行状态：${statuses[saved.status] ?? saved.status}（不代表业务验收）`,
      `验证记录：${evidence ? verdicts[evidence.verdict ?? "unknown"] ?? "未知" : "历史记录未提供验证证据"}`];
    const detail = summary.value?.detail;
    if (!detail) lines.push("运行文本回执：无法读取；请打开所属会话核对。其他已确认的记录仍列于下方。");
    else if (detail.text === null) lines.push("运行文本回执：没有可确认属于此运行的已保存文本。");
    else {
      lines.push("运行文本回执（原运行记录，内容未经核实）：", clip(detail.text, 4000) || "（文本为空）");
      if (detail.text_truncated || detail.text.length > 4000) lines.push("运行文本未完整加入草稿，请打开所属会话查看完整记录。");
    }
    if (evidence?.reason) lines.push(`验证记录原因：${clip(evidence.reason, 300)}`);
    if (evidence?.requires_review) lines.push("该记录提示需要人工核对。");
    if (result) {
      lines.push(`验证命令：${result.command ? clip(result.command, 400) : "未记录"}`);
      lines.push(`退出码：${result.exit_code == null ? "未知" : result.exit_code} · ${result.timed_out ? "超时" : result.exit_code == null ? "结果未知" : result.ok && result.exit_code === 0 ? "命令通过" : "命令未通过"}`);
      lines.push(`命令输出未加入草稿${result.stdout_truncated || result.stderr_truncated ? "；已保存输出存在截断" : ""}，请在原运行中查看。`);
    } else lines.push("未记录可用验证命令或结果。");
    const records = snapshot.steps.filter((step) => step.run_id === saved.id && step.session_id === session && step.status === "completed" &&
      step.payload?.source === "run_attachment_v1" && step.payload?.project_id === project && step.payload?.service_id === identity.service_id);
    const rows = records.map((step) => attachments.attachments.find((item) => item.id === step.payload?.attachment_id &&
      item.sha256 === step.payload?.sha256 && item.project_id === project && item.service_id === identity.service_id)).filter((item) => !!item);
    if (rows.length) {
      lines.push("已确认关联的项目附件（可能为复用；未读取原件，内容未验证）：");
      rows.slice(0, 5).forEach((item) => lines.push(`- ${clip(item.original_name, 200)} · ${item.id} · v${item.version}`));
      if (rows.length > 5) lines.push(`另有 ${rows.length - 5} 项已确认关联未列出。`);
    }
    if (records.length > rows.length) lines.push("部分关联未在当前项目已加载的附件记录中确认，未列出。");
    if (!records.length) lines.push("该运行未记录可确认的附件关联；不代表项目没有其他成果。");
    lines.push("后续目标：（请填写）");
    return { text: lines.join("\n"), service: identity.service_id };
  };
  const prepare = async () => {
    const ticket = ++epoch.current; setBusy(true); setPrepared(null); setMessage("");
    try { const value = await read(ticket); if (isCurrent(ticket)) setPrepared(value); }
    catch (error) { if (isCurrent(ticket)) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  };
  const append = async () => {
    if (!prepared || busy) return;
    const expected = prepared, ticket = ++epoch.current; setBusy(true); setMessage("");
    try {
      const value = await read(ticket);
      if (!isCurrent(ticket)) return;
      if (value.service !== expected.service || value.text !== expected.text) { setPrepared(null); throw new Error("运行证据或关联成果已变化，请重新准备。"); }
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text: value.text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") setPrepared(null);
    } catch (error) { if (isCurrent(ticket)) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 text-xs" data-testid={`run-followup-${run.id}`}>
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void prepare()}>准备后续任务草稿</Button>
    {prepared && <div className="space-y-2 rounded border border-line p-2"><p>以下是待追加的草稿，请核对；不会自动发送。</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-1 p-2">{prepared.text}</pre>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy} onClick={() => void append()}>追加到当前会话草稿</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPrepared(null)}>取消</Button></div></div>}
    {message && <p role="status" className="break-words text-ink-1">{message}</p>}
  </div>;
}
