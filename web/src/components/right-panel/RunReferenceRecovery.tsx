import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, ProjectRunSummaryResult, RunStepsResult } from "../../types/ipc";
import { Button, Textarea } from "../../ui";
import { RunAttachments } from "./RunAttachments";
import { requestConfirmation } from "../modals/ConfirmationDialog";

export type Reference = { version: 1; service_id: string; project_id: string; session_id: string; run_id: string };
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
function valid(value: unknown): value is Reference {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).sort().join(",") === "project_id,run_id,service_id,session_id,version" && v.version === 1 &&
    id(v.service_id) && id(v.project_id) && id(v.session_id) && id(v.run_id);
}
async function summary(ref: Reference): Promise<ProjectRunSummaryResult> {
  const result = await typedIPC.getProjectRunSummary({ project_id: ref.project_id, expected_service_id: ref.service_id, run_id: ref.run_id });
  if (result.service_id !== ref.service_id || result.project_id !== ref.project_id || result.detail?.id !== ref.run_id || result.detail.session_id !== ref.session_id)
    throw new Error("运行、项目或服务身份不匹配");
  return result;
}

/** User-saved locator; it contains identifiers, never a token or task content. */
export function RunReferenceExport({ run }: { run: AgentRun }): JSX.Element {
  const session = useSessionStore((s) => s.currentSessionId), project = useSessionStore((s) => s.currentProjectId);
  const base = ipc.agentBaseUrl, [record, setRecord] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  const epoch = useRef(0), running = useRef(false);
  useEffect(() => { epoch.current++; setRecord(""); setMessage(""); return () => { epoch.current++; }; }, [base, run.id, session, project]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setRecord(""); setMessage("连接已变化，请重新生成记录。"); }), []);
  const create = async () => {
    if (running.current || !project || !session || session !== run.session_id || run.metadata?.project_id !== project) return;
    running.current = true; setBusy(true); setMessage(""); setRecord("");
    const ticket = epoch.current, origin = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && origin === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const ref: Reference = { version: 1, service_id: identity.service_id, project_id: project, session_id: session, run_id: run.id };
      if (!valid(ref)) throw new Error("运行标识格式无效");
      await summary(ref);
      if (!current()) return;
      const after = await typedIPC.getServiceIdentity();
      if (!current() || after.service_id !== ref.service_id) throw new Error("服务身份已变化");
      setRecord(JSON.stringify(ref));
      setMessage("已生成定位记录；请自行保存。它不能认证物理主机，也不包含任务内容。");
    } catch { if (current()) setMessage("无法生成定位记录；请检查服务、项目与运行归属。"); }
    finally { running.current = false; setBusy(false); }
  };
  if (!project || !session || run.session_id !== session || run.metadata?.project_id !== project) return <></>;
  return <div className="space-y-1 text-xs"><Button size="sm" variant="secondary" disabled={busy} onClick={() => void create()}>生成运行定位记录</Button>
    {record && <><Textarea aria-label={`${run.id} 运行定位记录`} rows={3} readOnly value={record} /><Button size="sm" variant="secondary" onClick={() => void navigator.clipboard.writeText(record).then(() => setMessage("已复制定位记录，请自行保存。")).catch(() => setMessage("无法复制，请手动选择记录。"))}>复制记录</Button></>}
    {message && <p role="status">{message}</p>}</div>;
}

/** Read an existing run on the currently authenticated service, never resume it. */
export function RunReferenceRecovery({ onLocate }: { onLocate: (target: Reference & { host: string }) => void }): JSX.Element {
  const session = useSessionStore((s) => s.currentSessionId), project = useSessionStore((s) => s.currentProjectId);
  const base = ipc.agentBaseUrl, [input, setInput] = useState(""), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [result, setResult] = useState<{ ref: Reference; summary: ProjectRunSummaryResult; steps: RunStepsResult } | null>(null);
  const epoch = useRef(0), running = useRef(false);
  useEffect(() => { epoch.current++; setResult(null); setMessage(""); return () => { epoch.current++; }; }, [base, session, project]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setResult(null); setMessage("连接已变化，请重新定位运行。"); }), []);
  const find = async () => {
    if (running.current || input.length > 4096) return;
    running.current = true; setBusy(true); setResult(null); setMessage("");
    const ticket = ++epoch.current, origin = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && origin === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    try {
      const parsed: unknown = JSON.parse(input);
      if (!valid(parsed)) throw new Error("定位记录格式无效");
      const identity = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (identity.service_id !== parsed.service_id) throw new Error("当前服务身份不匹配");
      await summary(parsed);
      if (!current()) return;
      const steps = await typedIPC.getRunSteps(parsed.run_id);
      if (!current()) return;
      const latest = await summary(parsed);
      if (!current()) return;
      if (steps.run.id !== parsed.run_id || steps.run.session_id !== parsed.session_id || steps.run.metadata?.project_id !== parsed.project_id ||
          steps.steps.some((step) => step.run_id !== parsed.run_id || step.session_id !== parsed.session_id)) throw new Error("运行步骤归属不匹配");
      setResult({ ref: parsed, summary: latest, steps });
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : "无法读取运行"); }
    finally { running.current = false; setBusy(false); }
  };
  const locate = async () => {
    if (running.current || !result) return;
    const selected = result.ref;
    running.current = true; setBusy(true); setMessage("");
    const ticket = epoch.current, origin = ipc.agentBaseUrl;
    const current = () => ticket === epoch.current && origin === ipc.agentBaseUrl && useSessionStore.getState().currentSessionId === session && useSessionStore.getState().currentProjectId === project;
    try {
      const before = await typedIPC.getServiceIdentity();
      if (!current() || before.service_id !== selected.service_id) throw new Error("服务身份已变化");
      await summary(selected);
      const [snapshot, targetSession] = await Promise.all([typedIPC.getRunSteps(selected.run_id), typedIPC.getSession(selected.session_id)]);
      const again = await summary(selected);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      if (after.service_id !== selected.service_id || targetSession.session.id !== selected.session_id || targetSession.session.archived ||
          targetSession.session.project_id !== selected.project_id || again.detail?.session_id !== selected.session_id ||
          snapshot.run.id !== selected.run_id || snapshot.run.session_id !== selected.session_id || snapshot.run.metadata?.project_id !== selected.project_id ||
          snapshot.steps.some((step) => step.run_id !== selected.run_id || step.session_id !== selected.session_id)) throw new Error("服务、会话或运行归属已变化");
      if (session !== selected.session_id || project !== selected.project_id) {
        const accepted = await requestConfirmation({ title: "切换到原项目和会话？", description: "切换会离开当前输入内容并清除未发送的附件或正在读取的文件。请先保存需要保留的草稿；只打开已有运行，不会执行任务。", confirmLabel: "切换并查看" });
        if (!accepted || !current()) return;
        const identity = await typedIPC.getServiceIdentity();
        if (!current() || identity.service_id !== selected.service_id) throw new Error("服务身份已变化");
        await summary(selected);
        if (!current()) return;
      }
      const [freshSession, projects] = await Promise.all([typedIPC.getSession(selected.session_id), typedIPC.listProjects()]);
      const finalSummary = await summary(selected);
      if (!current()) return;
      if (freshSession.session.id !== selected.session_id || freshSession.session.archived ||
          freshSession.session.project_id !== selected.project_id || finalSummary.detail?.session_id !== selected.session_id)
        throw new Error("会话归属已变化");
      const selectedProject = projects.projects.find((item) => item.id === selected.project_id && !item.archived);
      if (!selectedProject) throw new Error("项目已归档或不可用");
      if (!useSessionStore.getState().selectVerifiedScope(freshSession.session, selectedProject)) return;
      onLocate({ ...selected, host: origin });
    } catch (error) { if (current()) setMessage(error instanceof Error ? error.message : "无法打开原运行"); }
    finally { running.current = false; setBusy(false); }
  };
  return <details className="border-b border-line p-3 text-xs" onToggle={(event) => { if (!event.currentTarget.open) { epoch.current++; setResult(null); setMessage(""); } }}>
    <summary>用定位记录找回运行</summary>
    <p>先连接并登录原远端服务，再粘贴记录。只读取原运行，不重跑或迁移；服务身份可能随数据库克隆，请自行确认实际主机。</p>
    <Textarea aria-label="运行定位记录" rows={3} value={input} disabled={busy} onChange={(event) => { setInput(event.target.value); setResult(null); setMessage(""); }} />
    <Button size="sm" variant="secondary" disabled={busy || !input.trim()} onClick={() => void find()}>核对并读取运行</Button>
    {message && <p role="status" className="break-words">{message}</p>}
    {result && <div className="space-y-2"><p className="break-all">运行 {result.ref.run_id} · 会话 {result.ref.session_id} · 项目 {result.ref.project_id}</p>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => void locate()}>进入原会话并定位运行</Button>
      <p>{result.summary.detail?.title || "未命名运行"} · 读取时状态 {result.summary.detail?.status}。状态和结果可能变化，连接成功不代表任务完成。</p>
      {result.summary.detail?.text && <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{result.summary.detail.text}</pre>}
      {result.summary.detail?.text_truncated && <p>结果预览已截断。</p>}
      <p>已记录步骤（最多显示 100 项）：</p><ol className="max-h-48 overflow-auto">{result.steps.steps.slice(0, 100).map((step) => <li key={step.id} className="break-words">{step.title} · {step.status} {step.summary}</li>)}</ol>
      {result.steps.steps.length > 100 && <p>步骤显示已截断。</p>}
      <RunAttachments run={result.steps.run} steps={result.steps.steps} reviewScope={{ sessionId: result.ref.session_id, projectId: result.ref.project_id, serviceId: result.ref.service_id }} />
    </div>}
  </details>;
}
