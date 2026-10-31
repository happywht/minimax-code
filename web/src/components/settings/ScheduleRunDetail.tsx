import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import type { RunGetResult, RunStepsResult, ScheduleOccurrence } from "../../types/ipc";
import { Button } from "../../ui";
import { RunAttachments } from "../right-panel/RunAttachments";
import { RunTextArtifacts } from "../right-panel/RunTextArtifacts";
import { FollowupRunDraft } from "../right-panel/FollowupRunDraft";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { useChat, usePermissionStore, useScheduleStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import { OpenRunDeliveries } from "../panels/OpenRunDeliveries";

interface Detail { snapshot: RunStepsResult; receipt: RunGetResult; serviceId: string; scopeKey: string }
const labels: Record<string, string> = { queued: "排队中", planning: "规划中", running: "运行中", awaiting_approval: "等待授权",
  waiting_input: "等待输入", completed: "执行结束", failed: "执行失败", cancelled: "已取消", interrupted: "运行中断", budget_paused: "预算暂停" };
const cancellable = ["queued", "planning", "running", "awaiting_approval", "waiting_input"];

export function ScheduleRunDetail({ occurrence, onVerified, onOpenSession, onFollowup, serviceId, activeJobRevision }: { occurrence: ScheduleOccurrence;
  onOpenSession?: () => boolean | void; serviceId?: string;
  onFollowup?: (occurrence: ScheduleOccurrence, text: string, status: string) => void;
  activeJobRevision?: number;
  onVerified?: (value: { serviceId: string; status: string } | null) => void }): JSX.Element {
  const activeSession = useSessionStore((state) => state.currentSessionId);
  const activeProject = useSessionStore((state) => state.currentProjectId);
  const [open, setOpen] = useState(false), [detail, setDetail] = useState<Detail | null>(null);
  const [includeResult, setIncludeResult] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false), [cancelMessage, setCancelMessage] = useState("");
  const cancelInFlight = useRef(false);
  const attentionInFlight = useRef(false);
  const [attentionBusy, setAttentionBusy] = useState(false);
  const [timelineBusy, setTimelineBusy] = useState(false);
  const timelineInFlight = useRef(false);
  const epoch = useRef(0);
  const project = occurrence.context?.project_id, host = ipc.agentBaseUrl;
  const scopeKey = JSON.stringify([occurrence.id, occurrence.run_id, occurrence.job_id, occurrence.revision,
    occurrence.context?.workspace, occurrence.context?.host, occurrence.context?.engine, project, host, activeSession, activeProject]);
  const eligible = !!occurrence.run_id && !!project && occurrence.context?.host === "local" && occurrence.context?.engine === "native" && !!occurrence.context?.workspace;
  useEffect(() => {
    setOpen(false); setDetail(null); setError(""); setBusy(false); setTimelineBusy(false); setCancelling(false); setCancelMessage(""); setIncludeResult(false); onVerified?.(null);
    const version = epoch;
    return () => { version.current++; };
  }, [scopeKey]);
  useEffect(() => ipc.onSideCar(() => { epoch.current++; setDetail(null); setIncludeResult(false); setBusy(false); onVerified?.(null); setError("连接变化，请重新读取本次运行。"); }), []);
  const load = async () => {
    if (!eligible || !occurrence.run_id || !project) return;
    const ticket = ++epoch.current, runId = occurrence.run_id;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    setBusy(true); setError(""); setDetail(null); setIncludeResult(false); onVerified?.(null);
    try {
      const [snapshot, receipt, attachments] = await Promise.all([
        typedIPC.getRunSteps(runId), typedIPC.getRun(runId), typedIPC.listAttachments(project),
      ]);
      if (!current()) return;
      const matches = (run: RunStepsResult["run"]) => {
        const schedule = run.metadata?.schedule as { occurrence_id?: string; job_id?: string; revision?: number } | undefined;
        return run.id === runId && run.session_id === occurrence.session_id && run.metadata?.project_id === project &&
          run.metadata?.workspace_snapshot === occurrence.context.workspace && run.metadata?.model_snapshot?.engine === "native" &&
          schedule?.occurrence_id === occurrence.id && schedule.job_id === occurrence.job_id && schedule.revision === occurrence.revision;
      };
      if (!attachments.service_id || !matches(snapshot.run) || !matches(receipt.run) ||
          snapshot.steps.some((step) => step.run_id !== runId || step.session_id !== occurrence.session_id)) throw new Error("运行、项目或发生记录关联不一致");
      setDetail({ snapshot, receipt, serviceId: attachments.service_id, scopeKey });
      if (snapshot.run.status === receipt.run.status) onVerified?.({ serviceId: attachments.service_id, status: snapshot.run.status });
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (current()) setBusy(false); }
  };
  const show = () => { if (open) { epoch.current++; setOpen(false); setDetail(null); setBusy(false); onVerified?.(null); } else { setOpen(true); void load(); } };
  const view = detail?.scopeKey === scopeKey ? detail : null;
  const run = view?.snapshot.run;
  const receipt = view?.receipt.result;
  const sameStatus = !!view && view.receipt.run.status === view.snapshot.run.status;
  const text = sameStatus && receipt && "text" in receipt && receipt.session_id === occurrence.session_id && receipt.run_id === occurrence.run_id && typeof receipt.text === "string" ? receipt.text : null;
  const linked = view?.snapshot.steps.filter((step) => step.status === "completed" && step.payload?.source === "run_attachment_v1" &&
    step.payload.project_id === project && step.payload.service_id === view.serviceId) ?? [];
  const openRequest = async (kind: "permission" | "question", requestId: string) => {
    if (!view || !run || !project || !serviceId || view.serviceId !== serviceId || attentionInFlight.current) return;
    const ticket = epoch.current, runId = run.id, sessionId = occurrence.session_id;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    const matches = (snapshot: RunStepsResult) => {
      const item = snapshot.run, schedule = item.metadata?.schedule as { occurrence_id?: string; job_id?: string; revision?: number } | undefined;
      return item.id === runId && item.session_id === sessionId && item.metadata?.project_id === project &&
        item.metadata?.workspace_snapshot === occurrence.context.workspace && item.metadata?.model_snapshot?.engine === "native" &&
        schedule?.occurrence_id === occurrence.id && schedule.job_id === occurrence.job_id && schedule.revision === occurrence.revision;
    };
    const findRequest = (snapshot: RunStepsResult) => kind === "permission"
      ? snapshot.pending_native_permissions?.find((item) => item.request_id === requestId && item.run_id === runId && item.session_id === sessionId && !item.tool.startsWith("codex:") && (item.remaining_ms === undefined || item.remaining_ms > 0))
      : (snapshot.pending_ask_users ?? (snapshot.pending_ask_user ? [snapshot.pending_ask_user] : [])).find((item) => item.request_id === requestId && item.run_id === runId && item.session_id === sessionId && (item.remaining_ms === undefined || item.remaining_ms > 0));
    attentionInFlight.current = true; setAttentionBusy(true); setError("");
    let navigated = false;
    try {
      const [identity, fresh] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(runId)]);
      if (!current()) return;
      if (identity.service_id !== serviceId || !matches(fresh) || !findRequest(fresh)) throw new Error("服务、运行或请求已变化；请刷新后核对");
      if (onOpenSession?.() === false) return;
      if (!useSessionStore.getState().setCurrentProject(project) || !useSessionStore.getState().setCurrent(sessionId)) return;
      navigated = true;
      const askVersion = useChat.getState().askRecoveryVersion();
      const permissionVersion = usePermissionStore.getState().recoveryVersion(sessionId);
      const [latestIdentity, latest] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getRunSteps(runId)]);
      if (ipc.agentBaseUrl !== host || useSessionStore.getState().currentSessionId !== sessionId || useSessionStore.getState().currentProjectId !== project) return;
      const request = findRequest(latest);
      if (latestIdentity.service_id !== serviceId || !matches(latest) || !request) throw new Error("请求已结束或运行关联已变化；请查看会话历史");
      if (kind === "permission") {
        const native = latest.pending_native_permissions;
        if (!native || native.some((item) => item.run_id !== runId || item.session_id !== sessionId || item.tool.startsWith("codex:"))) throw new Error("授权请求快照不完整");
        usePermissionStore.getState().reconcilePermissions(sessionId, [runId], native, null, permissionVersion);
        const state = usePermissionStore.getState(), selected = state.pending[requestId];
        if (!selected || selected.run_id !== runId || selected.session_id !== sessionId || state.resolving[requestId]) throw new Error("授权请求已变化");
        window.dispatchEvent(new CustomEvent("minimax:open-run-permission", { detail: { host, session: sessionId, run: runId, request: requestId } }));
      } else {
        const question = (latest.pending_ask_users ?? (latest.pending_ask_user ? [latest.pending_ask_user] : [])).find((item) => item.request_id === requestId && item.run_id === runId && item.session_id === sessionId);
        if (!question) throw new Error("问题已结束");
        useChat.getState().reconcileAskUser(question, askVersion, sessionId);
        if (useChat.getState().askUser?.request_id !== requestId) throw new Error("问题状态已变化，请重新读取");
      }
      void useSessionStore.getState().refresh({ loadCurrent: false });
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      if (navigated) toast.error("无法打开定时请求", message);
      else if (current()) setError(message);
    } finally { attentionInFlight.current = false; if (current()) setAttentionBusy(false); }
  };
  const openTimeline = async () => {
    if (!view || !run || !project || !serviceId || !onOpenSession || view.serviceId !== serviceId || timelineInFlight.current) return;
    const ticket = epoch.current, runId = run.id, sessionId = occurrence.session_id;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    timelineInFlight.current = true; setTimelineBusy(true); setError("");
    try {
      const [identity, snapshot, session] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getRunSteps(runId), typedIPC.getSession(sessionId),
      ]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const item = snapshot.run;
      const schedule = item.metadata?.schedule as { occurrence_id?: string; job_id?: string; revision?: number } | undefined;
      if (identity.service_id !== serviceId || after.service_id !== serviceId || item.id !== runId ||
          item.session_id !== sessionId || item.metadata?.project_id !== project ||
          item.metadata?.workspace_snapshot !== occurrence.context.workspace || item.metadata?.model_snapshot?.engine !== "native" ||
          schedule?.occurrence_id !== occurrence.id || schedule.job_id !== occurrence.job_id || schedule.revision !== occurrence.revision ||
          snapshot.steps.some((step) => step.run_id !== runId || step.session_id !== sessionId) ||
          session.session.id !== sessionId || session.session.project_id !== project)
        throw new Error("服务、运行或会话归属已变化，请重新读取。");
      if (onOpenSession() === false) return;
      const scope = useSessionStore.getState();
      if (scope.currentProjectId !== project && !scope.setCurrentProject(project)) return;
      if (useSessionStore.getState().currentSessionId !== sessionId && !useSessionStore.getState().setCurrent(sessionId)) return;
      void useSessionStore.getState().refresh({ loadCurrent: false });
      window.dispatchEvent(new CustomEvent("minimax:open-session-timeline", { detail: { host, service: serviceId, project, session: sessionId, run: runId, job: occurrence.job_id, occurrence: occurrence.id, revision: occurrence.revision, workspace: occurrence.context.workspace! } }));
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { timelineInFlight.current = false; if (current()) setTimelineBusy(false); }
  };
  const cancel = async () => {
    if (!view || !run || !sameStatus || !cancellable.includes(run.status) || !project || cancelInFlight.current) return;
    const ticket = epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    cancelInFlight.current = true; setCancelling(true); setCancelMessage("");
    try {
      const confirmed = await requestConfirmation({ title: "停止本次定时运行？",
        description: "只请求停止此运行，不停用未来计划；已经发生的文件或外部操作不会撤销。停止后请核对结果和副作用。",
        confirmLabel: "请求停止" });
      if (!confirmed || !current()) return;
      const identity = await typedIPC.getServiceIdentity();
      if (!current() || identity.service_id !== view.serviceId) throw new Error("服务或会话已变化，请重新读取");
      const reply = await typedIPC.cancelRun(run.id, { service_id: view.serviceId, job_id: occurrence.job_id,
        occurrence_id: occurrence.id, session_id: occurrence.session_id, project_id: project, revision: occurrence.revision });
      if (!current()) return;
      setCancelMessage(reply.requested ? "已请求停止；这不代表运行已结束，请刷新并核对最终状态。" : "当前服务未接受停止请求；请刷新并核对实际状态。");
      setCancelling(false);
      await load();
    } catch (failure) {
      if (current()) { setDetail(null); onVerified?.(null); setCancelMessage(`停止结果未确认：${failure instanceof Error ? failure.message : String(failure)}。请先刷新并核对实际状态。`); }
    } finally { cancelInFlight.current = false; if (current()) setCancelling(false); }
  };
  const pauseAndCancel = async () => {
    if (!view || !run || !sameStatus || !cancellable.includes(run.status) || !project || !serviceId ||
        serviceId !== view.serviceId || !activeJobRevision || cancelInFlight.current) return;
    const ticket = epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === activeSession && useSessionStore.getState().currentProjectId === activeProject;
    cancelInFlight.current = true; setCancelling(true); setCancelMessage("");
    let pauseConfirmed = false;
    try {
      const confirmed = await requestConfirmation({ title: "停用计划并请求停止本次运行？",
        description: "先停用未来计划，再请求停止当前运行。停止不一定立即完成，已产生的文件或外部操作不会撤销；若任一步结果不明，请刷新并核对。",
        confirmLabel: "停用并请求停止" });
      if (!confirmed || !current()) return;
      const identity = await typedIPC.getServiceIdentity();
      if (!current() || identity.service_id !== serviceId) throw new Error("服务或会话已变化");
      const job = useScheduleStore.getState().jobs.find((item) => item.id === occurrence.job_id);
      if (!job || job.revision !== activeJobRevision || job.active_occurrence_id !== occurrence.id) throw new Error("计划或当前发生已变化");
      const paused = await typedIPC.disableJob(job.id, { expected_revision: activeJobRevision,
        expected_service_id: serviceId, expected_occurrence_id: occurrence.id });
      if (!current()) { toast.info("停用结果待核对", "请刷新计划和本次运行；未继续请求停止。"); return; }
      if (paused.job.id !== job.id || paused.job.enabled || paused.job.revision !== activeJobRevision || paused.job.active_occurrence_id !== occurrence.id)
        throw new Error("停用回执不匹配");
      pauseConfirmed = true;
      void useScheduleStore.getState().refresh();
      const reply = await typedIPC.cancelRun(run.id, { service_id: serviceId, job_id: occurrence.job_id,
        occurrence_id: occurrence.id, session_id: occurrence.session_id, project_id: project, revision: occurrence.revision });
      if (!current()) { toast.info("停止结果待核对", "请刷新计划和运行状态。"); return; }
      setCancelMessage(reply.requested ? "计划已停用，已请求停止本次运行；请刷新确认最终状态和副作用。" : "计划已停用；停止请求未被接受，请刷新核对运行状态。");
      setCancelling(false);
      await load();
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      if (current()) setCancelMessage(`${pauseConfirmed ? "计划已停用；停止" : "停用"}结果未确认：${message}。请刷新计划及运行并核对后再操作。`);
      else toast.info("操作结果待核对", "请刷新计划和本次运行，确认是否停用及停止。");
    } finally { cancelInFlight.current = false; if (current()) setCancelling(false); }
  };
  return <div className="mt-1 rounded border border-line p-2" data-testid={`schedule-run-detail-${occurrence.id}`}>
    <p>执行位置：{occurrence.context?.host === "local" ? "当前服务所在主机" : "未知主机"} / {occurrence.context?.engine === "native" ? "Native" : "未知引擎"} · 项目 {project || "未知"} · 服务地址 {host}</p>
    {occurrence.context?.workspace && <p className="break-all">运行时目录：{occurrence.context.workspace}</p>}
    {!occurrence.run_id ? <p>尚未接单，没有可查看的运行或关联成果。</p> : !eligible ? <p>旧记录缺少可核实的本机、项目或目录绑定，不能直接查看关联成果。</p> : <Button type="button" size="sm" variant="secondary" onClick={show}>{open ? "收起本次运行" : "查看本次运行结果与成果"}</Button>}
    {open && <div className="space-y-1">
      {busy && <p role="status">正在读取本次运行…</p>}
      {error && <p role="alert">{error}；请刷新或打开该会话核对。</p>}
      {cancelMessage && <p role="status">{cancelMessage}</p>}
      {run && view && <>
        <p>读取时运行状态：{labels[run.status] ?? run.status} · 运行 {run.id} · 服务 {view.serviceId}</p>
        {serviceId === view.serviceId && onOpenSession && <Button type="button" size="sm" variant="secondary" disabled={timelineBusy} loading={timelineBusy} onClick={() => void openTimeline()}>打开原会话时间线</Button>}
        {project && serviceId === view.serviceId && occurrence.context.workspace && <OpenRunDeliveries
          host={host} service={serviceId} project={project} session={occurrence.session_id} run={run.id}
          schedule={{ job: occurrence.job_id, occurrence: occurrence.id, revision: occurrence.revision, workspace: occurrence.context.workspace }} />}
        {run.error && <p>运行错误：{run.error}</p>}
        {!sameStatus && <p>读取期间运行状态发生变化，请刷新后查看回执。</p>}
        {sameStatus && serviceId === view.serviceId && <div className="space-y-1">
          {(view.snapshot.pending_native_permissions ?? []).filter((item) => item.run_id === run.id && item.session_id === occurrence.session_id).map((item) => <p key={item.request_id}>待授权：{item.tool} <Button type="button" size="sm" disabled={attentionBusy} onClick={() => void openRequest("permission", item.request_id)}>打开此授权请求</Button></p>)}
          {(view.snapshot.pending_ask_users ?? (view.snapshot.pending_ask_user ? [view.snapshot.pending_ask_user] : [])).filter((item) => item.run_id === run.id && item.session_id === occurrence.session_id).map((item) => <p key={item.request_id}>待回答：{item.questions[0]?.header || item.questions[0]?.question || "Agent 提问"} <Button type="button" size="sm" disabled={attentionBusy} onClick={() => void openRequest("question", item.request_id)}>打开此问题</Button></p>)}
        </div>}
        {sameStatus && (text !== null ? <><p>已保存的运行结果：</p><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words">{text.slice(0, 8000) || "（结果文本为空）"}</pre>{text.length > 8000 && <p>此处仅显示前 8000 字符；完整结果请打开会话。</p>}</> : <p>{["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) ? "运行尚未结束或结果尚未保存。" : "没有可确认归属于此运行的文本回执。"}</p>)}
        {linked.length ? <RunAttachments run={run} steps={view.snapshot.steps} reviewScope={{ sessionId: occurrence.session_id, projectId: project!, serviceId: view.serviceId }} /> : <p>没有已记录的任务关联附件；旧成果或未关联文件不会列在这里。</p>}
        {project && serviceId === view.serviceId && activeProject === project ? <details className="mt-2">
          <summary>本次运行登记关联的文本成果</summary>
          <RunTextArtifacts key={`${host}:${view.serviceId}:${project}:${occurrence.session_id}:${run.id}`} runId={run.id} sessionId={occurrence.session_id} projectId={project} serviceId={view.serviceId} review />
        </details> : <p>选择该运行所属项目并重新确认服务后，可读取登记关联的文本成果。</p>}
        {sameStatus && ["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(run.status) && serviceId === view.serviceId && <div className="mt-2 space-y-1 rounded border border-line p-2">
          <p>将本次结果用于后续任务：目标是当前会话，必须属于同一项目；</p><p>草稿不会自动发送，后续运行使用当前配置、预算和授权。</p>
          {activeProject !== project || !activeSession ? <p>请先选择该计划所属项目的目标会话，再返回这里准备草稿。</p> :
            <FollowupRunDraft run={run} allowOtherSession scheduleScope={{ serviceId: view.serviceId, jobId: occurrence.job_id,
              occurrenceId: occurrence.id, revision: occurrence.revision, workspace: occurrence.context.workspace! }} />}
        </div>}
        {sameStatus && text && onFollowup && serviceId === view.serviceId && ["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(run.status) && <div className="mt-2 space-y-1 rounded border border-line p-2">
          <p>可将结果前 2000 字符用于新的定时计划草稿。内容可能含凭据或敏感信息；先核对。</p><p>计划默认停用，不会自动创建或运行；创建时重新核对项目、配置、预算和权限。</p>
          <label className="flex items-start gap-2"><input type="checkbox" checked={includeResult} onChange={(event) => setIncludeResult(event.target.checked)} />选择上述结果摘录用于后续计划草稿</label>
          <Button type="button" size="sm" variant="secondary" disabled={!includeResult} onClick={() => onFollowup(occurrence, text, run.status)}>准备后续定时计划草稿</Button>
        </div>}
        {sameStatus && cancellable.includes(run.status) && <Button type="button" size="sm" variant="danger" disabled={busy || cancelling} loading={cancelling} onClick={() => void cancel()}>停止本次运行</Button>}
        {sameStatus && cancellable.includes(run.status) && activeJobRevision && serviceId === view.serviceId && <Button type="button" size="sm" variant="danger" disabled={busy || cancelling} loading={cancelling} onClick={() => void pauseAndCancel()}>停用计划并请求停止本次运行</Button>}
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>刷新本次运行</Button>
      </>}
    </div>}
  </div>;
}
