import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRunStep, VerificationEvidence } from "../../types/ipc";
import { Button } from "../../ui";

type Draft = { text: string; evidence: string };
const drafts = new Map<string, Draft>();
let unloadGuard = false;
const warnBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
function saveDraft(key: string, draft: Draft) {
  drafts.set(key, draft);
  if (!unloadGuard) { window.addEventListener("beforeunload", warnBeforeUnload); unloadGuard = true; }
}
function removeDraft(key: string) {
  drafts.delete(key);
  if (!drafts.size && unloadGuard) { window.removeEventListener("beforeunload", warnBeforeUnload); unloadGuard = false; }
}
const clip = (value: string, limit: number) => value.length > limit ? `${value.slice(0, limit)}\n（摘要截断，原始记录请另行核对）` : value;
const groups = [["added", "新增"], ["modified", "修改"], ["deleted", "删除"]] as const;

function changes(steps: AgentRunStep[]): string[] {
  const rows = steps.filter((step) => step.kind === "observation" && step.status === "completed" && step.payload?.source === "run_file_changes");
  if (!rows.length) return ["没有已保存的采样记录；未启用、未完成或未保存均不能视为零变化。"];
  return rows.flatMap((step, index) => {
    const payload = step.payload!;
    const lines = [`采样记录 ${index + 1}（步骤 ${step.ordinal}）`];
    if (payload.available !== true) return [...lines, payload.available === false ? "采样不可用，变化未知。" : "采样格式无法确认，变化未知。"]; 
    if (groups.some(([key]) => !Array.isArray(payload[key]) || (payload[key] as unknown[]).length > 1000 || !(payload[key] as unknown[]).every((path) => typeof path === "string")))
      return [...lines, "采样记录不完整，变化未知。"]; 
    for (const [key, label] of groups) {
      const paths = payload[key] as string[];
      lines.push(`${label}：${paths.length} 项`);
      paths.slice(0, 100).forEach((path) => lines.push(`- ${clip(path, 160)}`));
      if (paths.length > 100) lines.push(`另有 ${paths.length - 100} 项未列出，请查看原记录。`);
    }
    return lines;
  });
}

export function RunDeliverySummary({ host, service, project, session, run }: {
  host: string; service: string; project: string; session: string; run: string;
}): JSX.Element {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const key = JSON.stringify([host, service, project, activeSession, session, run]);
  const [draft, setDraft] = useState<Draft | null>(() => drafts.get(key) ?? null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    ++epoch.current; setDraft(drafts.get(key) ?? null); setBusy(false); setMessage("");
    return () => { ++epoch.current; };
  }, [key]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setBusy(false); setMessage("连接已变化；草稿保留，下载前会重新核对来源。"); }), []);
  useEffect(() => useSessionStore.subscribe((state) => {
    if (state.currentProjectId !== project || state.currentSessionId !== activeSession || ipc.agentBaseUrl !== host) {
      ++epoch.current; setBusy(false);
    }
  }), [project, activeSession, host]);
  const current = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === activeSession;
  const read = async (ticket: number): Promise<string> => {
    const [before, snapshot, source, summary] = await Promise.all([
      typedIPC.getServiceIdentity(), typedIPC.getRunSteps(run), typedIPC.getSession(session),
      typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: service, run_id: run }),
    ]);
    const [after, sourceAfter] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
    const item = summary.detail, saved = snapshot.run;
    if (!current(ticket) || !service || before.service_id !== service || after.service_id !== service ||
        source.session.id !== session || source.session.project_id !== project || source.session.archived ||
        sourceAfter.session.id !== session || sourceAfter.session.project_id !== project || sourceAfter.session.archived ||
        summary.project_id !== project || summary.service_id !== service || !item || item.id !== run || item.session_id !== session ||
        saved.id !== run || saved.session_id !== session || saved.metadata?.project_id !== project || item.status !== saved.status ||
        !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 || snapshot.steps.some((step) => step.run_id !== run || step.session_id !== session))
      throw new Error("服务、运行或项目会话绑定已变化，请重新选择。");
    const evidence = saved.metadata?.verification as VerificationEvidence | undefined;
    const valid = evidence?.version === 1 ? evidence : undefined, validation = valid?.validation;
    const lines = ["运行交付摘要（草稿，需人工核对）", `服务地址：${host}`, `服务身份：${service}`, `项目：${project}`,
      `来源会话：${session}`, `运行：${run}`, `记录版本：${snapshot.cursor}`, `标题：${clip(saved.title || "未命名", 240)}`,
      `运行状态：${saved.status}`, `执行目录（运行记录）：${typeof saved.metadata?.workspace_snapshot === "string" ? clip(saved.metadata.workspace_snapshot, 500) : "未记录"}`,
      "", "一、已保存的运行文本（模型文本未经核验，不是验证证据）"];
    if (item.text === null) lines.push("没有可确认归属的文本回执。");
    else { lines.push(clip(item.text, 4000) || "（已保存文本为空）"); if (item.text_truncated) lines.push("（服务端回执已截断）"); }
    lines.push("", "二、已保存的验证记录（不等于业务验收）");
    if (!valid) lines.push("未保存可识别的验证证据，状态未知。");
    else {
      lines.push(`记录结论：${valid.verdict ?? "未知"} · 需要核对：${valid.requires_review === true ? "是" : valid.requires_review === false ? "否" : "未知"}`);
      lines.push(`原因：${typeof valid.reason === "string" ? clip(valid.reason, 500) : "未记录"}`);
      if (!validation) lines.push("未记录可用验证命令及结果。");
      else {
        lines.push(`命令：${typeof validation.command === "string" && validation.command ? clip(validation.command, 500) : "未记录"}`,
          `退出码：${validation.exit_code ?? "未知"} · 超时：${validation.timed_out === true ? "是" : validation.timed_out === false ? "否" : "未记录"} · 记录的 ok：${typeof validation.ok === "boolean" ? String(validation.ok) : "未知"}`);
        if (validation.error) lines.push(`记录错误：${clip(validation.error, 1000)}`);
        for (const stream of ["stdout", "stderr"] as const) {
          const value = validation[stream], truncated = validation[stream === "stdout" ? "stdout_truncated" : "stderr_truncated"];
          lines.push(`${stream}：${typeof value === "string" ? clip(value, 2000) || "（已保存为空）" : "未记录"}${truncated === true ? "\n（保存的输出已截断）" : ""}`);
        }
      }
    }
    lines.push("", "三、任务前后文件采样", "仅比较有界范围内的 Git 可见普通文件，忽略文件与私有运行数据不在范围内；只保存路径，不保存文件内容或补丁。同期用户或其他任务的修改可能包含在内，不能归因。", ...changes(snapshot.steps),
      "", "四、用户补充", "已核实内容：", "未完成事项：", "需进一步核对：");
    return lines.join("\n");
  };
  const prepare = async () => {
    if (busy || (draft && !window.confirm("重新准备会替换当前摘要编辑。请先下载或复制需要保留的内容；确定继续吗？"))) return;
    const ticket = ++epoch.current; setBusy(true); setMessage("");
    try {
      const evidence = await read(ticket);
      if (!current(ticket)) return;
      const value = { text: evidence, evidence }; saveDraft(key, value); setDraft(value);
      setMessage("摘要已准备，请编辑并检查敏感信息；尚未发送。");
    } catch (reason) { if (current(ticket)) setMessage(`未能准备：${reason instanceof Error ? reason.message : String(reason)}`); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const download = async () => {
    if (!draft || busy) return;
    const ticket = ++epoch.current, expected = draft; setBusy(true); setMessage("");
    try {
      const evidence = await read(ticket);
      if (!current(ticket)) return;
      if (evidence !== expected.evidence) throw new Error("来源记录已变化；请复制现有编辑后重新准备。");
      const url = URL.createObjectURL(new Blob([expected.text], { type: "text/plain;charset=utf-8" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "run-delivery-summary.txt";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("已请求浏览器下载；内容未发送。");
    } catch (reason) { if (current(ticket)) setMessage(`下载未开始：${reason instanceof Error ? reason.message : String(reason)}`); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <section className="mt-2 space-y-2 rounded border border-line p-2 text-xs" aria-label="运行交付摘要">
    <h4 className="font-medium">运行交付摘要</h4>
    <p>整理已保存的运行文本、验证回执和文件采样，不读取文件全文。请核对来源与敏感信息；草稿可编辑，不自动发送。</p>
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void prepare()}>{draft ? "重新准备摘要" : "准备交付摘要"}</Button>
    {draft && <><label className="block">摘要草稿<textarea aria-label="交付摘要草稿" className="mt-1 block min-h-64 w-full rounded border border-line bg-surface-0 p-2" value={draft.text} disabled={busy} maxLength={100000} onChange={(event) => { const value = { ...draft, text: event.target.value }; saveDraft(key, value); setDraft(value); }} /></label>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy || !draft.text.trim()} onClick={() => void download()}>{busy ? "正在核对…" : "核对来源并下载文本"}</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => { if (window.confirm("放弃此摘要草稿？未下载的编辑会丢失。")) { removeDraft(key); setDraft(null); setMessage("草稿已放弃。"); } }}>放弃草稿</Button></div>
      <p>草稿按服务、项目、当前会话及来源运行保存在本页面内存中；刷新或关闭页面会丢失，未放弃的草稿会触发离开提醒。下载前会重新读取来源，变化时保留编辑并要求重新准备。</p></>}
    {message && <p role="status" className="break-words">{message}</p>}
  </section>;
}
