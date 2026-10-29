import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, TextArtifact, VerificationEvidence } from "../../types/ipc";
import { Button } from "../../ui";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted", "budget_paused"]);
const statuses: Record<string, string> = { completed: "运行结束", failed: "失败", cancelled: "已取消", interrupted: "已中断", budget_paused: "预算暂停" };
const verdicts: Record<string, string> = { passed: "配置的验证通过", failed: "验证未通过", unknown: "未知", not_applicable: "不适用" };
const clip = (text: string, limit: number) => text.length > limit ? `${text.slice(0, limit)}…（已截断）` : text;

type OutputRef = { output_ref: string; service_id: string; run_id: string; session_id: string; project_id: string; chars: number; sha256: string; label: string };
type OutputPage = { id: string; service_id: string; run_id: string; session_id: string; project_id: string; chars: number; sha256: string; offset: number; next_offset: number; page: string };
type Prepared = { text: string; service: string; artifacts: TextArtifact[]; more: boolean; outputs: OutputRef[] };
type SelectedArtifact = { row: TextArtifact; preview: string; truncated: boolean };

/** Prepares editable context from a persisted run; it never sends a task. */
export function FollowupRunDraft({ run, allowOtherSession = false }: { run: AgentRun; allowOtherSession?: boolean }): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.currentProjectId);
  const host = ipc.agentBaseUrl;
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [selected, setSelected] = useState<SelectedArtifact | null>(null);
  const [output, setOutput] = useState<{ ref: OutputRef; page: OutputPage } | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setPrepared(null); setSelected(null); setOutput(null); setMessage(""); setBusy(false); return () => { ++epoch.current; }; }, [run.id, session, project, host]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setPrepared(null); setSelected(null); setOutput(null); setBusy(false); setMessage("连接变化，请重新准备草稿。"); }), []);
  const sourceSession = run.session_id;
  const available = !!project && !!session && (allowOtherSession || sourceSession === session) && run.metadata?.project_id === project && terminal.has(run.status);
  if (!available || !project || !session) return null;
  const isCurrent = (ticket: number) => ticket === epoch.current && ipc.agentBaseUrl === host &&
    useSessionStore.getState().currentProjectId === project && useSessionStore.getState().currentSessionId === session;
  const read = async (ticket: number): Promise<Prepared> => {
    const identity = await typedIPC.getServiceIdentity();
    if (!isCurrent(ticket) || !identity.service_id) throw new Error("服务或会话已变化");
    const [snapshot, attachments, target, source, summary, artifacts] = await Promise.all([
      typedIPC.getRunSteps(run.id), typedIPC.listAttachments(project), typedIPC.getSession(session), typedIPC.getSession(sourceSession),
      typedIPC.getProjectRunSummary({ project_id: project, expected_service_id: identity.service_id, run_id: run.id })
        .then((value) => ({ value }), (error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          if (/Project or service identity changed|Run project association changed/i.test(message)) throw error;
          return { value: null };
        }),
      typedIPC.listArtifacts(project, run.id).then((value) => ({ value }), () => ({ value: null })),
    ]);
    const [after, targetAfter, sourceAfter] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getSession(sourceSession)]);
    if (!isCurrent(ticket) || after.service_id !== identity.service_id || attachments.service_id !== identity.service_id ||
        (artifacts.value !== null && (artifacts.value.service_id !== identity.service_id ||
          artifacts.value.artifacts.some((item) => item.project_id !== project || item.service_id !== identity.service_id || item.run_id !== run.id))) ||
        target.session.id !== session || target.session.project_id !== project || target.session.archived ||
        targetAfter.session.id !== session || targetAfter.session.project_id !== project || targetAfter.session.archived ||
        source.session.id !== sourceSession || source.session.project_id !== project || source.session.archived ||
        sourceAfter.session.id !== sourceSession || sourceAfter.session.project_id !== project || sourceAfter.session.archived ||
        (sourceSession !== session && summary.value === null) ||
        snapshot.run.id !== run.id || snapshot.run.session_id !== sourceSession || snapshot.run.metadata?.project_id !== project || !terminal.has(snapshot.run.status) ||
        (summary.value !== null && (summary.value.project_id !== project || summary.value.service_id !== identity.service_id || !summary.value.detail ||
        summary.value.detail.id !== run.id || summary.value.detail.session_id !== sourceSession || summary.value.detail.status !== snapshot.run.status)))
      throw new Error("运行、项目或服务归属已变化，请刷新");
    const saved = snapshot.run, raw = saved.metadata?.verification as VerificationEvidence | undefined;
    const evidence = raw?.version === 1 ? raw : undefined;
    const result = evidence?.validation;
    const lines = ["请基于以下已记录运行准备后续任务（目标请补充；请先核对原记录与文件）：",
      `来源会话：${sourceSession} · 运行：${saved.id} · ${clip(saved.title || "未命名", 240)}`, `运行状态：${statuses[saved.status] ?? saved.status}（不代表业务验收）`,
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
    if (saved.status !== "completed") {
      const reviewSteps = snapshot.steps.filter((step) => step.run_id === saved.id && step.session_id === sourceSession &&
        (step.payload?.outcome === "unknown" || step.payload?.requires_review === true ||
          (step.kind === "tool_call" && step.status !== "completed")));
      lines.push("待核对的已记录步骤（状态不是副作用的证明，请检查文件及外部系统）：");
      reviewSteps.slice(-20).forEach((step) => lines.push(`- ${clip(step.tool_name || step.title || step.kind, 200)} · ${step.status}${step.payload?.outcome === "unknown" || step.payload?.requires_review === true ? " · 记录要求核对或结果未知" : ""}`));
      if (!reviewSteps.length) lines.push("未找到符合条件的记录；不代表没有未记录的操作或副作用。");
      if (reviewSteps.length > 20) lines.push(`另有 ${reviewSteps.length - 20} 项未列出，请查看完整时间线。`);
      lines.push("已核对的进展：（请填写）", "未完成事项：（请填写）", "不确定的操作及外部副作用：（请核对后填写）");
      lines.push("预算与未知用量：（请核对原运行及子任务；未知不等于零）");
    }
    const records = snapshot.steps.filter((step) => step.run_id === saved.id && step.session_id === sourceSession && step.status === "completed" &&
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
    if (!artifacts.value) lines.push("登记关联的文本成果暂时无法读取，请在原运行中核对。");
    else if (!artifacts.value.artifacts.length) lines.push("该运行没有已登记的文本成果关联；不代表项目没有其他成果。");
    lines.push("后续目标：（请填写）");
    const outputs: OutputRef[] = [];
    for (const step of snapshot.steps) {
      const meta = step.payload?.metadata as Record<string, unknown> | undefined;
      const ref = meta?.tool_output as Record<string, unknown> | undefined;
      if (step.run_id === saved.id && step.session_id === sourceSession && ref &&
          typeof ref.output_ref === "string" && ref.run_id === saved.id && ref.session_id === sourceSession &&
          ref.project_id === project && ref.service_id === identity.service_id && typeof ref.sha256 === "string" &&
          typeof ref.chars === "number" && Number.isSafeInteger(ref.chars) && ref.chars >= 0) {
        outputs.push({ output_ref: ref.output_ref, run_id: saved.id, session_id: sourceSession, project_id: project,
          service_id: identity.service_id, sha256: ref.sha256, chars: ref.chars, label: step.tool_name || step.title || "工具输出" });
      }
    }
    return { text: lines.join("\n"), service: identity.service_id, artifacts: artifacts.value?.artifacts ?? [], more: !!artifacts.value?.next_cursor, outputs };
  };
  const readSelected = async (row: TextArtifact, expected: Prepared, ticket: number): Promise<SelectedArtifact> => {
    const before = await read(ticket);
    const listed = before.artifacts.find((item) => item.id === row.id && item.sha256 === row.sha256 && item.snapshot_id === row.snapshot_id && item.version === row.version);
    if (!listed || before.service !== expected.service || before.text !== expected.text) throw new Error("运行或成果关联已变化，请重新准备。");
    const result = await typedIPC.getArtifact(project, row.id);
    const after = await read(ticket);
    const fresh = result.artifact;
    if (!isCurrent(ticket) || after.service !== expected.service || after.text !== expected.text ||
        !after.artifacts.some((item) => item.id === row.id && item.sha256 === row.sha256 && item.snapshot_id === row.snapshot_id && item.version === row.version) ||
        fresh.id !== row.id || fresh.project_id !== project || fresh.service_id !== expected.service || fresh.run_id !== run.id ||
        fresh.sha256 !== row.sha256 || fresh.snapshot_id !== row.snapshot_id || fresh.version !== row.version || fresh.source_path !== row.source_path ||
        typeof result.preview_text !== "string") throw new Error("成果内容或归属已变化，请重新准备。");
    return { row: fresh, preview: result.preview_text.slice(0, 4000), truncated: result.preview_text.length > 4000 };
  };
  const readOutput = async (ref: OutputRef, expected: Prepared, ticket: number, offset: number): Promise<OutputPage> => {
    const before = await read(ticket);
    const matches = (value: Prepared) => value.service === expected.service && value.text === expected.text &&
      value.outputs.some((item) => item.output_ref === ref.output_ref && item.sha256 === ref.sha256 && item.chars === ref.chars);
    if (!matches(before)) throw new Error("运行或输出关联已变化，请重新准备。");
    const page = await ipc.request<OutputPage>("run.output_page", { run_id: run.id, session_id: sourceSession,
      output_ref: ref.output_ref, expected_service_id: expected.service, offset, limit: 4000 });
    const after = await read(ticket);
    if (!isCurrent(ticket) || !matches(after) || page.id !== ref.output_ref || page.run_id !== run.id ||
        page.session_id !== sourceSession || page.project_id !== project || page.service_id !== expected.service ||
        page.sha256 !== ref.sha256 || page.chars !== ref.chars || page.offset !== offset ||
        !Number.isSafeInteger(page.next_offset) || page.next_offset < offset || page.next_offset > page.chars || typeof page.page !== "string")
      throw new Error("输出内容或归属已变化，请重新准备。");
    return page;
  };
  const selectOutput = async (ref: OutputRef, offset: number) => {
    if (!prepared || busy) return;
    const ticket = ++epoch.current; setBusy(true); setOutput(null); setMessage("");
    try { const page = await readOutput(ref, prepared, ticket, offset); if (isCurrent(ticket)) setOutput({ ref, page }); }
    catch (error) { if (isCurrent(ticket)) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  };
  const selectArtifact = async (row: TextArtifact) => {
    if (!prepared || busy) return;
    const ticket = ++epoch.current; setBusy(true); setSelected(null); setMessage("");
    try { const value = await readSelected(row, prepared, ticket); if (isCurrent(ticket)) setSelected(value); }
    catch (error) { if (isCurrent(ticket)) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  };
  const prepare = async () => {
    const ticket = ++epoch.current; setBusy(true); setPrepared(null); setSelected(null); setOutput(null); setMessage("");
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
      let text = value.text;
      if (selected) {
        const fresh = await readSelected(selected.row, expected, ticket);
        if (!isCurrent(ticket)) return;
        if (fresh.preview !== selected.preview || fresh.truncated !== selected.truncated) { setSelected(null); throw new Error("成果预览已变化，请重新选择并核对。"); }
        text += `\n\n用户选择的关联文本成果（内容未经验证）：${fresh.row.source_path} · v${fresh.row.version} · ${fresh.row.id} · SHA-256 ${fresh.row.sha256}\n${fresh.preview || "（文本为空）"}${fresh.truncated ? "\n（仅加入前 4000 个字符，请查看原成果。）" : ""}`;
      }
      if (output) {
        const fresh = await readOutput(output.ref, expected, ticket, output.page.offset);
        if (!isCurrent(ticket)) return;
        if (fresh.page !== output.page.page || fresh.next_offset !== output.page.next_offset) { setOutput(null); throw new Error("工具输出已变化，请重新选择并核对。"); }
        text += `\n\n用户选择的已捕获工具输出（来源运行 ${statuses[run.status] ?? run.status}；内容未经验证，工具结果及副作用可能不确定；捕获前可能截断）：${output.ref.label} · ${output.ref.output_ref} · SHA-256 ${output.ref.sha256}\n字符 ${fresh.offset}–${fresh.next_offset} / ${fresh.chars}：\n${fresh.page || "（文本为空）"}\n仅加入所选页，请核对完整输出和原始记录。`;
      }
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setMessage(detail.result ?? "当前聊天输入区未打开，请先切换到聊天再操作。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") { setPrepared(null); setSelected(null); setOutput(null); }
    } catch (error) { if (isCurrent(ticket)) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 text-xs" data-testid={`run-followup-${run.id}`}>
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void prepare()}>准备后续任务草稿</Button>
    {prepared && <div className="space-y-2 rounded border border-line p-2"><p>来源会话：{sourceSession}；目标会话：{session}。请核对以下草稿；不会自动发送。</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-1 p-2">{prepared.text}</pre>
      {!!prepared.artifacts.length && <div className="space-y-1"><p>选择一项登记关联的文本成果，读取并核对后可随草稿追加。关联不证明由该运行生成。</p>
        <div className="flex flex-wrap gap-1">{prepared.artifacts.map((row) => <Button key={row.id} type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void selectArtifact(row)}>{row.source_path} · v{row.version}</Button>)}</div>
        {prepared.more && <p>还有更早的成果，请在原运行中查看。</p>}
      </div>}
      {selected && <div className="space-y-1 rounded border border-line p-2"><p>已选：{selected.row.source_path} · v{selected.row.version}；内容未经验证。</p>
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words" aria-label="后续任务成果预览">{selected.preview || "（文本为空）"}</pre>
        {selected.truncated && <p>仅预览并加入前 4000 个字符，请查看原成果。</p>}
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setSelected(null)}>移除此成果</Button></div>}
      {!!prepared.outputs.length && <div className="space-y-1"><p>选择已捕获的工具输出并核对一页，可加入草稿。输出可能不完整，状态和副作用需检查原记录及文件。</p>
        <div className="flex flex-wrap gap-1">{prepared.outputs.map((ref) => <Button key={ref.output_ref} type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void selectOutput(ref, 0)}>{ref.label} · {ref.output_ref}</Button>)}</div>
      </div>}
      {output && <div className="space-y-1 rounded border border-line p-2"><p>已选工具输出：{output.ref.label}；字符 {output.page.offset}–{output.page.next_offset} / {output.page.chars}。仅加入当前页，捕获可能已截断。</p>
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words" aria-label="后续任务工具输出预览">{output.page.page || "（文本为空）"}</pre>
        <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy || output.page.offset === 0} onClick={() => void selectOutput(output.ref, Math.max(0, output.page.offset - 4000))}>上一页</Button>
          <Button type="button" size="sm" variant="secondary" disabled={busy || output.page.next_offset >= output.page.chars} onClick={() => void selectOutput(output.ref, output.page.next_offset)}>下一页</Button>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setOutput(null)}>移除此输出</Button></div></div>}
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={busy} onClick={() => void append()}>追加到当前会话草稿</Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setPrepared(null)}>取消</Button></div></div>}
    {message && <p role="status" className="break-words text-ink-1">{message}</p>}
    {message === "已添加到当前会话草稿，尚未发送。" && <Button type="button" size="sm" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
  </div>;
}
