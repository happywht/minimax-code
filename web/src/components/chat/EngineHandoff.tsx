import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, PersistedMessage, Session, VerificationEvidence } from "../../types/ipc";
import { Button, Modal, Textarea } from "../../ui";
import { allowScopeChange } from "../../lib/scopeChangeGuard";

type Engine = "native" | "codex";
type Snapshot = { source: Session; messages: PersistedMessage[]; runs: AgentRun[]; service: string; fingerprint: string; targetRoot: string | null };
type Evidence = { run: string; service: string; cursor: number; text: string };
const active = new Set(["queued", "planning", "running", "awaiting_approval", "waiting_input"]);
const terminal = new Set(["completed", "failed", "cancelled", "interrupted", "budget_paused"]);
const DRAFT_PREFIX = "minimax-code:draft:";

export function EngineHandoff({ sessionId, target, onClose }: { sessionId: string; target: Engine; onClose: () => void }): JSX.Element {
  const currentProject = useSessionStore((s) => s.currentProjectId);
  const currentSession = useSessionStore((s) => s.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null), [text, setText] = useState("");
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false), [created, setCreated] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [evidence, setEvidence] = useState<Evidence | null>(null), [included, setIncluded] = useState<Evidence | null>(null);
  const epoch = useRef(0), creating = useRef(false), live = useRef(true);
  const originProject = useRef(currentProject), originHost = useRef(host);
  const current = (ticket: number) => live.current && ticket === epoch.current && ipc.agentBaseUrl === originHost.current &&
    useSessionStore.getState().currentSessionId === sessionId && useSessionStore.getState().currentProjectId === originProject.current;
  useEffect(() => {
    live.current = true;
    const off = ipc.onSideCar(() => {
      ++epoch.current; setBusy(false);
      if (creating.current) setUncertain(true);
      setNotice("连接变化；交接尚未确认，请核对当前服务与会话列表。");
    });
    return () => { live.current = false; ++epoch.current; off(); };
  }, []);
  const read = async (ticket: number): Promise<Snapshot> => {
    const identity = await typedIPC.getServiceIdentity();
    if (!current(ticket) || !identity.service_id) throw new Error("服务或当前会话已变化");
    const [saved, recent, running, projects] = await Promise.all([
      typedIPC.getSession(sessionId), typedIPC.listMessages(sessionId, { limit: 12 }), typedIPC.listRuns({ session_id: sessionId, limit: 20 }), typedIPC.listProjects(),
    ]);
    const after = await typedIPC.getServiceIdentity();
    const custom = saved.session.workspace_mode === "worktree" || !!saved.session.workspace_path;
    const targetRoot = projects.projects.find((project) => project.id === (saved.session.project_id ?? "inbox"))?.root_path || null;
    if (!current(ticket) || after.service_id !== identity.service_id || saved.session.id !== sessionId ||
        (saved.session.project_id ?? "inbox") !== (originProject.current ?? "inbox") || saved.session.archived ||
        (saved.session.engine ?? "native") === target ||
        running.runs.some((item) => item.session_id !== sessionId)) throw new Error("会话、项目、服务或工作区不再符合交接条件");
    if (custom && (!saved.session.workspace_path || !targetRoot)) throw new Error("来源工作区路径或项目根目录未配置；请先核对来源会话并为项目设置明确根目录。");
    const fingerprint = JSON.stringify([saved.session.id, saved.session.project_id, saved.session.engine, saved.session.workspace_mode,
      saved.session.workspace_path, targetRoot, recent.messages, running.runs.map((run) => [run.id, run.status])]);
    return { source: saved.session, messages: recent.messages, runs: running.runs, service: identity.service_id, fingerprint, targetRoot };
  };
  const prepare = async () => {
    if (busy || uncertain || created) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const value = await read(ticket); if (!current(ticket)) return;
      const items = value.messages.filter((message) => message.role === "user" || message.role === "assistant");
      const lines = ["引擎交接草稿（由最近持久消息摘录组成，不是完整历史；请审阅和修改）：",
        `来源会话：${sessionId} · 原引擎：${value.source.engine ?? "native"} · 目标引擎：${target}`,
        "任务目标与用户约束：（请补充或核对）", "当前进展和待处理事项：（请补充或核对）", "近期消息摘录："];
      for (const item of items) {
        const content = typeof item.text === "string" ? item.text : typeof item.content === "string" ? item.content : "";
        const truncated = content.length > 350;
        lines.push(`[${item.role === "user" ? "用户" : "助手"}${item.streaming ? "，未完成" : ""}] ${content.slice(0, 350)}${truncated ? "…（摘录截断）" : ""}`);
      }
      if (!items.length) lines.push("（最近记录中没有用户或助手文本）");
      lines.push("仅取最近最多 12 条消息；工具结果、附件和引擎内部状态未迁移。请核对敏感信息、未完成内容及文件状态。");
      setSnapshot(value); setText(lines.join("\n")); setEvidence(null); setIncluded(null); setAcknowledged(false);
      setNotice("请审阅摘录并补充目标、约束；删除不希望带入的信息。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const readEvidence = async (runId: string, service: string, ticket: number): Promise<Evidence> => {
    const [before, record] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(runId)]);
    const after = await typedIPC.getServiceIdentity();
    const saved = record.run;
    if (!current(ticket) || before.service_id !== service || after.service_id !== service ||
        saved.id !== runId || saved.session_id !== sessionId || (saved.metadata?.project_id ?? "inbox") !== (originProject.current ?? "inbox") ||
        !terminal.has(saved.status) || !Number.isSafeInteger(record.cursor) || record.cursor < 0 ||
        record.steps.some((step) => step.run_id !== runId || step.session_id !== sessionId)) throw new Error("运行、服务或项目已变化，请重新读取验证记录。");
    const raw = saved.metadata?.verification as VerificationEvidence | undefined;
    if (raw?.version !== 1) throw new Error("该运行没有可用的结构化验证记录。");
    const validation = raw.validation;
    const text = [
      `来源运行 ${runId} · 状态 ${saved.status} · 验证记录 ${raw.verdict ?? "unknown"}`,
      `命令：${validation?.command ? validation.command.slice(0, 400) : "未记录"}${validation?.command && validation.command.length > 400 ? "（截断）" : ""}`,
      `退出码：${validation?.exit_code ?? "未知"} · 超时：${validation?.timed_out === true ? "是" : validation?.timed_out === false ? "否" : "未知"}`,
      `输出截断：${validation?.stdout_truncated === true || validation?.stderr_truncated === true ? "是" : validation ? "见原记录" : "未知"}`,
      "以上是原运行保存的验证记录，不证明当前文件未变化或业务验收通过；请查看原记录。",
    ].join("\n");
    return { run: runId, service, cursor: record.cursor, text };
  };
  const selectEvidence = async (runId: string) => {
    if (!snapshot || busy || included) return;
    const ticket = ++epoch.current; setBusy(true); setEvidence(null); setNotice("");
    try { const item = await readEvidence(runId, snapshot.service, ticket); if (current(ticket)) setEvidence(item); }
    catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (live.current) setBusy(false); }
  };
  const includeEvidence = async () => {
    if (!evidence || busy || included) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const fresh = await readEvidence(evidence.run, evidence.service, ticket);
      if (!current(ticket)) return;
      if (JSON.stringify(fresh) !== JSON.stringify(evidence)) throw new Error("验证记录已变化，请重新选择。");
      const addition = `\n\n用户选择的原运行验证记录：\n${fresh.text}`;
      if (text.length + addition.length > 8000) throw new Error("交接内容超过 8000 字，请先精简。");
      setText(text + addition); setIncluded(fresh); setEvidence(null); setAcknowledged(false);
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (live.current) setBusy(false); }
  };
  useEffect(() => { void prepare(); }, []); // Initial read is explicitly opened by the user.
  const start = async () => {
    if (!snapshot || busy || uncertain || created || !text.trim() || text.length > 8000 || !acknowledged || creating.current) return;
    if (!allowScopeChange()) return;
    const ticket = ++epoch.current, handoff = text; setBusy(true); setNotice("");
    let dispatched = false;
    try {
      const [fresh, engines] = await Promise.all([read(ticket), typedIPC.listEngines()]);
      if (!current(ticket)) return;
      if (fresh.service !== snapshot.service || fresh.fingerprint !== snapshot.fingerprint) throw new Error("原会话记录或运行状态已变化；请重新准备并审阅，当前编辑仍保留。");
      if (included && JSON.stringify(await readEvidence(included.run, included.service, ticket)) !== JSON.stringify(included))
        throw new Error("已加入的验证记录已变化；请重新准备并审阅，当前编辑仍保留。");
      if (!engines.engines.some((engine) => engine.id === target && engine.available)) throw new Error("目标引擎当前不可用，未创建会话。");
      dispatched = true; creating.current = true;
      const custom = fresh.source.workspace_mode === "worktree" || !!fresh.source.workspace_path;
      const result = await typedIPC.createSession({ title: `交接 · ${fresh.source.title}`.slice(0, 100), project_id: originProject.current ?? "inbox",
        engine: target, expected_service_id: fresh.service, handoff_source_session_id: sessionId,
        ...(custom ? { handoff_source_workspace_mode: fresh.source.workspace_mode ?? "local", handoff_source_workspace_path: fresh.source.workspace_path!, handoff_target_project_root: fresh.targetRoot! } : {}) });
      creating.current = false;
      if (!current(ticket)) { if (live.current) { setUncertain(true); setNotice("创建结果可能已保存，但当前上下文变化；请核对会话列表，交接文本仍在本窗口。"); } return; }
      const newSession = result.session;
      if (!newSession || result.reused !== false || result.session_id === sessionId || newSession.id !== result.session_id ||
          (newSession.project_id ?? "inbox") !== (originProject.current ?? "inbox") || newSession.engine !== target ||
          newSession.workspace_mode !== "local" || newSession.workspace_path) throw new Error("新会话返回的绑定不匹配，请核对会话列表。");
      setCreated(newSession.id);
      const finalIdentity = await typedIPC.getServiceIdentity();
      if (!current(ticket) || finalIdentity.service_id !== fresh.service) throw new Error("创建后服务或会话发生变化；请核对新会话，交接文本保留在此。");
      const key = DRAFT_PREFIX + newSession.id;
      if (window.localStorage.getItem(key) !== null) throw new Error("新会话已有本地草稿，未覆盖；请复制本窗口文本后手动处理。");
      window.localStorage.setItem(key, handoff);
      useSessionStore.getState().mergeSessions([newSession]);
      if (!useSessionStore.getState().setCurrent(newSession.id)) throw new Error("未切换会话；交接文本已保存到新会话草稿。");
      onClose();
    } catch (error) {
      if (live.current) {
        if (dispatched && !created) setUncertain(true);
        setNotice(`${error instanceof Error ? error.message : String(error)}${dispatched ? " 如结果不明，请先核对会话列表，不要重复创建。" : ""}`);
      }
    } finally { creating.current = false; if (live.current) setBusy(false); }
  };
  const close = () => {
    if (busy) return;
    if (snapshot && text && !window.confirm("关闭将丢失此处尚未写入新会话的交接编辑。请先复制需要保留的内容；确定关闭吗？")) return;
    onClose();
  };
  return <Modal title={`切换到 ${target === "codex" ? "Codex" : "Native"} 并交接`} onClose={close} widthClass="max-w-2xl" testId="engine-handoff">
    <div className="space-y-3 text-sm">
      <p>创建同项目的新会话；不会修改原会话，不迁移工具状态、权限、预算或 Codex 线程，也不自动发送。新会话使用项目默认目录。</p>
      <p>这里只展示有界历史摘录，不是自动验证的总结。可能包含密码、隐私或其他敏感内容；请删除不应带入的内容并补充约束。</p>
      {snapshot && <p className="break-all text-xs text-ink-2">项目：{originProject.current ?? "inbox"} · 服务：{snapshot.service} · 地址：{originHost.current} · 来源：{sessionId}</p>}
      {snapshot && (snapshot.source.workspace_mode === "worktree" || !!snapshot.source.workspace_path) && <p className="break-all text-status-warning">来源工作区：{snapshot.source.workspace_path}（{snapshot.source.workspace_mode === "worktree" ? "独立工作区" : "自定义目录"}）；新会话项目目录：{snapshot.targetRoot}。不会复制或合并文件；两个目录可能不同，请先核对文件与原运行。</p>}
      {snapshot && (snapshot.runs.some((run) => active.has(run.status)) || snapshot.messages.some((item) => item.streaming)) && <p role="status" className="text-status-warning">原会话仍有运行或未完成消息；交接仅是读取时快照，不会停止原任务。</p>}
      {snapshot && <p className="text-xs text-ink-2">只检查最近 20 条运行及 12 条消息，更早记录未检查。</p>}
      {snapshot && !included && <div className="space-y-1 rounded border border-line p-2 text-xs">
        <p>可选择原会话的运行，读取并审阅其已保存验证记录；不会自动加入交接。</p>
        <div className="flex flex-wrap gap-1">{snapshot.runs.filter((run) => terminal.has(run.status)).slice(0, 5).map((run) => <Button key={run.id} type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void selectEvidence(run.id)}>{run.title || run.id} · {run.status}</Button>)}</div>
        {evidence && <><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words">{evidence.text}</pre><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void includeEvidence()}>加入此验证记录</Button></>}
      </div>}
      {included && <p className="text-xs">已加入运行 {included.run} 的记录；可在下方编辑，创建前将重新核对来源。</p>}
      {snapshot && <Textarea aria-label="可编辑的引擎交接内容" rows={12} maxLength={8000} disabled={busy} value={text} onChange={(event) => { setText(event.target.value); setAcknowledged(false); }} />}
      {snapshot && <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />我已审阅摘录、约束与敏感信息，理解内部状态不会迁移。</label>}
      {notice && <p role="status" className="break-words text-status-warning">{notice}</p>}
      {created && <p className="break-all">已创建新会话：{created}。如草稿未写入，请复制上方文本并手动处理。</p>}
      {uncertain && !created && <p>创建结果不明。请先核对会话列表；只有确认不存在后才允许重新创建。</p>}
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" disabled={!snapshot || busy || uncertain || !!created || !acknowledged || !text.trim()} loading={busy} onClick={() => void start()}>创建新会话并保存草稿</Button>
        <Button type="button" size="sm" variant="secondary" disabled={busy || uncertain || !!created} onClick={() => { if (window.confirm("重新读取会替换当前交接编辑内容，确定继续？")) void prepare(); }}>重新准备</Button>
        {(uncertain || created) && <Button type="button" size="sm" variant="secondary" onClick={() => { void navigator.clipboard?.writeText(text).then(() => setNotice("交接文本已复制；请手动核对会话。"), () => setNotice("复制失败，请手动选择文本。")); }}>复制交接文本</Button>}
        {uncertain && !created && <Button type="button" size="sm" variant="secondary" onClick={() => { if (window.confirm("已检查目标项目的会话列表，确认本次没有创建会话？")) { setUncertain(false); setAcknowledged(false); } }}>已检查，允许重新创建</Button>}</div>
      {currentSession !== sessionId || currentProject !== originProject.current ? <p role="status" className="text-status-warning">当前会话或项目已切换；本次交接不可继续，请复制或关闭。</p> : null}
    </div>
  </Modal>;
}
