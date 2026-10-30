import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Clock3,
  FileCode2,
  Loader2,
  MessageSquareText,
  ShieldQuestion,
  TerminalSquare,
} from "lucide-react";
import { usePermissionStore, useRunTimelineStore, useSessionStore } from "../../stores";
import type { AgentRunStep, AgentRunStepKind, AgentRunStepStatus, RunStepsResult } from "../../types/ipc";
import { buildPermissionPatchFiles } from "../../lib/permissionPatchPreview";
import { PermissionPatchPreview } from "../modals/PermissionPatchPreview";
import { CodexFileChangePreview, permissionArgsWithoutCodexPreview } from "../modals/CodexFileChangePreview";
import { strings } from "../../ui/strings";
import { Button } from "../../ui";
import { RunRecoveryActions, recoveryDraftKey, readRetainedRecovery, discardRetainedRecovery } from "./RunRecoveryActions";
import { RunBudgetPanel } from "./RunBudgetPanel";
import { VerificationBadge } from "../chat/VerificationBadge";
import { VerificationDetails } from "../chat/VerificationDetails";
import { RunWorkspaceDiff } from "./RunWorkspaceDiff";
import { NativeSteerInput } from "./NativeSteerInput";
import { RunAttachments } from "./RunAttachments";
import { FollowupRunDraft } from "./FollowupRunDraft";
import type { VerificationEvidence } from "../../types/ipc";
import { ipc, typedIPC } from "../../ipc";
import { StreamEvent } from "../../types/ipc";
import { OutputStorage } from "./OutputStorage";
import { RunTimelineTextArtifacts } from "./RunTimelineTextArtifacts";
import { CodexInputImages } from "./CodexInputImages";
import { CodexInputDraft } from "./CodexInputDraft";
import { PlanRunDraft } from "./PlanRunDraft";
import { RunReferenceExport, RunReferenceRecovery, type Reference } from "./RunReferenceRecovery";
import { ProjectRunBudget } from "../panels/ProjectRunBudget";
import { RunTextArtifacts } from "./RunTextArtifacts";

export type TimelineTarget = { host: string; service: string; project: string; session: string; run: string;
  job: string; occurrence: string; revision: number; workspace: string };

export interface RunTimelinePanelProps {
  testId?: string;
  target?: TimelineTarget | null;
  onClearTarget?: () => void;
}

export function RunTimelinePanel({
  testId = "run-timeline-panel", target, onClearTarget,
}: RunTimelinePanelProps): JSX.Element {
  const init = useRunTimelineStore((s) => s.init);
  const loadForSession = useRunTimelineStore((s) => s.loadForSession);
  const loading = useRunTimelineStore((s) => s.loading);
  const error = useRunTimelineStore((s) => s.error);
  const runs = useRunTimelineStore((s) => s.runs);
  const order = useRunTimelineStore((s) => s.order);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const currentProjectId = useSessionStore((s) => s.currentProjectId);
  const [referenceTarget, setReferenceTarget] = useState<(Reference & { host: string }) | null>(null);
  useEffect(() => {
    if (referenceTarget && (referenceTarget.host !== ipc.agentBaseUrl || referenceTarget.project_id !== currentProjectId || referenceTarget.session_id !== currentSessionId)) setReferenceTarget(null);
  }, [referenceTarget, currentProjectId, currentSessionId]);
  const pendingPermissions = usePermissionStore((s) => s.pending);
  const resolvePermission = usePermissionStore((s) => s.resolve);

  useEffect(() => {
    init();
  }, [init]);

  useEffect(() => {
    // A different session is a fresh context: re-arm follow mode so the
    // panel lands on the newest events instead of staying wherever the
    // user happened to scroll in the previous session.
    setIsFollowing(true);
    setHasNewEvents(false);
    void loadForSession(currentSessionId);
  }, [currentSessionId, loadForSession]);

  const visibleRuns = useMemo(
    () => order.map((id) => runs[id]).filter((r) => r && !!currentSessionId && r.session_id === currentSessionId),
    [order, runs, currentSessionId],
  );

  const pending = Object.values(pendingPermissions).filter((request) => !!currentSessionId && request.session_id === currentSessionId).sort((a, b) => a.received_at - b.received_at);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isFollowing, setIsFollowing] = useState(true);
  const [hasNewEvents, setHasNewEvents] = useState(false);
  const eventCount = pending.length + visibleRuns.reduce((total, run) => total + run.steps.length, 0);

  const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
    const el = scrollRef.current;
    if (!el) return;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior });
    } else {
      el.scrollTop = el.scrollHeight;
    }
    setIsFollowing(true);
    setHasNewEvents(false);
  };

  useEffect(() => {
    if (eventCount === 0) return;
    if (isFollowing) {
      scrollToBottom();
    } else {
      setHasNewEvents(true);
    }
    // Only eventCount should drive this effect; isFollowing is read to decide whether
    // the latest event should pull the panel down.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventCount]);

  useEffect(() => {
    // Session history replaced (initial load or session switch): jump to
    // the latest events without animation. eventCount alone is not a
    // reliable trigger here — two sessions can produce the same step
    // count, which would leave the panel stranded mid-scroll.
    if (loading || eventCount === 0) return;
    if (isFollowing) {
      scrollToBottom("auto");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceFromBottom <= 50;
    setIsFollowing(nearBottom);
    if (nearBottom) setHasNewEvents(false);
  };

  const syncError = error ? (
    <div role="alert" className="mx-3 my-2 rounded-md border border-status-warning/30 bg-surface-1 px-3 py-2 text-xs text-status-warning">
      <p>{strings.rightPanel.timeline.unavailable} {error}</p>
      {visibleRuns.length > 0 && <p>{strings.rightPanel.timeline.stale}</p>}
      <Button size="sm" variant="ghost" loading={loading}
        onClick={() => void loadForSession(currentSessionId)}>{strings.common.retry}</Button>
    </div>
  ) : null;

  if (target && target.host === ipc.agentBaseUrl && target.project === currentProjectId && target.session === currentSessionId) {
    return <TargetRunTimeline key={JSON.stringify(target)} target={target} onClear={onClearTarget} />;
  }

  if (referenceTarget && referenceTarget.host === ipc.agentBaseUrl && referenceTarget.project_id === currentProjectId && referenceTarget.session_id === currentSessionId) {
    return <ReferenceTargetTimeline key={JSON.stringify(referenceTarget)} target={referenceTarget} onClear={() => setReferenceTarget(null)} />;
  }

  if (visibleRuns.length === 0 && pending.length === 0) {
    return (
      <div data-testid={testId} className="px-3 py-4 text-xs text-minimax-muted">
        <OutputStorage />
        <RunReferenceRecovery onLocate={setReferenceTarget} />
        {loading
          ? strings.rightPanel.timeline.loading
          : error
            ? syncError
            : strings.rightPanel.timeline.empty}
      </div>
    );
  }

  return (
    <div data-testid={testId} className="relative">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div><h3 className="text-xs font-semibold text-ink-0">任务时间线</h3><p className="text-[11px] text-ink-2">当前会话的运行与已记录证据</p></div>
        <OutputStorage />
      </div>
      {syncError}
      <RunReferenceRecovery onLocate={setReferenceTarget} />
      <div
        ref={scrollRef}
        data-testid="run-timeline-scroll"
        onScroll={handleScroll}
        className="max-h-[min(58vh,520px)] overflow-y-auto divide-y divide-minimax-border"
      >
        {pending.map((request) => {
          const patchFiles = buildPermissionPatchFiles(request.tool, request.args);
          return (
            <div
              key={request.request_id}
              data-testid="run-timeline-approval"
              className="px-3 py-2.5"
            >
              <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2">
                <div className="flex items-center gap-1.5 text-xs font-medium text-amber-200">
                  <ShieldQuestion size={13} />
                  <span className="break-all">等待授权 · {request.tool}</span>
                </div>
                <ExpandablePre
                  text={formatArgs(permissionArgsWithoutCodexPreview(request.args))}
                  collapsedText={summarizeArgs(permissionArgsWithoutCodexPreview(request.args))}
                  className="mt-1 text-amber-100/80"
                  testId="run-timeline-approval-args"
                />
                <CodexFileChangePreview tool={request.tool} args={request.args} />
                <PermissionPatchPreview
                  files={patchFiles}
                  testId="run-timeline-approval-patch-preview"
                />
                <p className="mt-1 text-[11px] text-ink-2">请核对请求内容后选择；拒绝或允许只处理此请求。</p>
                <div className="mt-2 flex flex-wrap justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => void resolvePermission(request.request_id, "deny")}
                    className="rounded border border-minimax-border px-2 py-1 text-[11px] text-minimax-fg hover:border-red-500/40 hover:text-status-error"
                  >
                    {strings.rightPanel.timeline.deny}
                  </button>
                  <button
                    type="button"
                    onClick={() => void resolvePermission(request.request_id, "allow")}
                    className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/25"
                  >
                    {strings.rightPanel.timeline.allow}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
        {visibleRuns.map((run) => (
          <article key={run.id} className="space-y-3 px-3 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="break-words text-xs font-semibold text-ink-0">
                  {run.title || strings.rightPanel.timeline.runFallbackTitle}
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-minimax-muted">
                  {run.id.slice(0, 12)}
                </div>
              </div>
              <RunStatusBadge status={run.status} />
            </div>
            {run.status === "queued" && run.metadata?.cancel_requested === true && <p role="status" className="rounded border border-status-warning/30 bg-surface-1 p-2 text-xs text-status-warning">已请求取消，等待前一个任务完成清理；此任务不会开始执行。</p>}
            {run.status === "awaiting_approval" && <p className="rounded border border-status-warning/30 bg-surface-1 p-2 text-xs text-status-warning">此任务等待授权；请在当前会话的授权请求中处理。</p>}
            {run.status === "waiting_input" && <p className="rounded border border-status-warning/30 bg-surface-1 p-2 text-xs text-status-warning">此任务等待回答；请查看聊天中的问题卡片。</p>}
            {run.status === "budget_paused" && <p className="rounded border border-status-warning/30 bg-surface-1 p-2 text-xs text-status-warning">预算已暂停；请查看预算记录及下方可用操作。</p>}
            {["failed", "interrupted", "cancelled"].includes(run.status) && <p className="rounded border border-status-warning/30 bg-surface-1 p-2 text-xs text-status-warning">请核对已记录的步骤与文件，再决定是否继续。</p>}
            <div className="space-y-1" aria-label="验证记录">
            {!["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) && <VerificationBadge evidence={run.metadata?.verification as VerificationEvidence | undefined} />}
            {!["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) && <VerificationDetails evidence={run.metadata?.verification as VerificationEvidence | undefined} />}
            </div>
            {run.queuedInput && <details className="my-2 text-xs"><summary>已接收、尚未开始的任务内容</summary>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{run.queuedInput.text}</pre>
              {run.queuedInput.truncated && <p>内容预览已截断。</p>}
            </details>}
            <div><h4 className="mb-1 text-[11px] font-semibold text-ink-1">执行步骤</h4>
            {run.steps.length === 0 && <p className="text-[11px] text-ink-2">暂无已记录步骤。</p>}
            <ol className="space-y-1.5">
              {run.steps.map((step) => (
                <TimelineStep key={step.id} step={step} runStatus={run.status} />
              ))}
            </ol></div>
            <div className="space-y-2 border-t border-line pt-2"><h4 className="text-[11px] font-semibold text-ink-1">预算、成果与操作</h4>
            <RunReferenceExport run={run} />
            <RunBudgetPanel run={run} />
            <CodexInputImages run={run} />
            <CodexInputDraft run={run} />
            <PlanRunDraft run={run} />
            <RunAttachments run={run} steps={run.steps} />
            <RunTimelineTextArtifacts run={run} />
            <FollowupRunDraft run={run} />
            <NativeSteerInput run={run} />
            <RunWorkspaceDiff run={run} />
            <RunRecoveryActions run={run} refresh={() => loadForSession(currentSessionId)} /></div>
          </article>
        ))}
      </div>
      {hasNewEvents && (
        <button
          type="button"
          data-testid="run-timeline-new-events"
          onClick={() => scrollToBottom()}
          className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full border border-minimax-border bg-minimax-panel/95 px-2.5 py-1 text-[11px] font-medium text-minimax-fg shadow-lg backdrop-blur transition-colors duration-200 hover:bg-minimax-border"
        >
          {strings.rightPanel.timeline.newEvents}
        </button>
      )}
    </div>
  );
}

function ReferenceTargetTimeline({ target, onClear }: { target: Reference & { host: string }; onClear: () => void }): JSX.Element {
  const editing = useRef(false);
  const [snapshot, setSnapshot] = useState<RunStepsResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const load = async () => {
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === target.host &&
      useSessionStore.getState().currentProjectId === target.project_id && useSessionStore.getState().currentSessionId === target.session_id;
    if (!editing.current) setSnapshot(null);
    setBusy(true); setError("");
    try {
      const check = async () => {
        const info = await typedIPC.getProjectRunSummary({ project_id: target.project_id, expected_service_id: target.service_id, run_id: target.run_id });
        if (info.service_id !== target.service_id || info.project_id !== target.project_id || info.detail?.id !== target.run_id || info.detail.session_id !== target.session_id) throw new Error("运行归属已变化");
      };
      await check();
      const [result, session, projects] = await Promise.all([typedIPC.getRunSteps(target.run_id), typedIPC.getSession(target.session_id), typedIPC.listProjects()]);
      await check();
      if (!current()) return;
      if (!projects.projects.some((project) => project.id === target.project_id && !project.archived) || session.session.id !== target.session_id || session.session.project_id !== target.project_id || session.session.archived ||
          result.run.id !== target.run_id || result.run.session_id !== target.session_id || result.run.metadata?.project_id !== target.project_id ||
          !Number.isSafeInteger(result.cursor) || result.cursor < 0 || result.steps.some((step) => step.run_id !== target.run_id || step.session_id !== target.session_id)) throw new Error("服务、会话或运行归属已变化");
      setSnapshot(result);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : "无法读取目标运行"); }
    finally { if (current()) setBusy(false); }
  };
  useEffect(() => { void load(); const off = ipc.onSideCar(() => { epoch.current++; if (!editing.current) setSnapshot(null); setBusy(false); setError("连接已变化，请重新读取目标运行；恢复交接会保留，状态变化时需重新核对。"); }); return () => { epoch.current++; off(); }; }, [target]);
  return <div className="space-y-2 p-3 text-xs" data-testid="reference-run-timeline">
    <h3 className="font-semibold">已定位的运行时间线</h3><p className="break-all">运行：{target.run_id} · 会话：{target.session_id}</p>
    <div className="flex gap-2"><Button size="sm" variant="secondary" disabled={busy} onClick={() => void load()}>刷新此运行</Button><Button size="sm" variant="ghost" onClick={onClear}>返回近期时间线</Button></div>
    {busy && <p role="status">正在核对并读取运行…</p>}{error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {(!!error || (snapshot && !supportsTargetRecovery(snapshot))) && <RetainedTargetRecovery host={target.host} service={target.service_id} project={target.project_id} session={target.session_id} run={target.run_id} />}
    {snapshot && <fieldset disabled={busy || !!error} hidden={busy || !!error} className="space-y-2"><article className="space-y-2"><p>{snapshot.run.title || "未命名运行"} · 读取时状态 {snapshot.run.status}</p>
      <p>运行状态可能变化；恢复需核对并明确确认，不会自动执行或迁移任务。</p>
      <ol className="space-y-1.5">{[...snapshot.steps].sort((a, b) => a.ordinal - b.ordinal || a.id.localeCompare(b.id)).map((step) => <TimelineStep key={step.id} step={step} runStatus={snapshot.run.status} />)}</ol>
      {!snapshot.steps.length && <p>暂无已记录步骤。</p>}
      <RunAttachments run={snapshot.run} steps={snapshot.steps} reviewScope={{ sessionId: target.session_id, projectId: target.project_id, serviceId: target.service_id }} verifyRun />
      <RunTimelineTextArtifacts run={snapshot.run} />
      <TargetRecoveryEvidence snapshot={snapshot} host={target.host} service={target.service_id} project={target.project_id} session={target.session_id} refresh={load} onEditingChange={(value) => { editing.current = value; }} />
    </article></fieldset>}
  </div>;
}

function TargetRunTimeline({ target, onClear }: { target: TimelineTarget; onClear?: () => void }): JSX.Element {
  const editing = useRef(false);
  const [snapshot, setSnapshot] = useState<RunStepsResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const load = async () => {
    const ticket = ++epoch.current;
    const current = () => ticket === epoch.current && ipc.agentBaseUrl === target.host &&
      useSessionStore.getState().currentProjectId === target.project && useSessionStore.getState().currentSessionId === target.session;
    if (!editing.current) setSnapshot(null);
    setBusy(true); setError("");
    try {
      const [before, result, session] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getRunSteps(target.run), typedIPC.getSession(target.session),
      ]);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const run = result.run;
      const schedule = run.metadata?.schedule as { job_id?: string; occurrence_id?: string; revision?: number } | undefined;
      if (!target.service || before.service_id !== target.service || after.service_id !== target.service ||
          session.session.id !== target.session || session.session.project_id !== target.project || session.session.archived ||
          run.id !== target.run || run.session_id !== target.session || run.metadata?.project_id !== target.project ||
          run.metadata?.workspace_snapshot !== target.workspace || run.metadata?.model_snapshot?.engine !== "native" ||
          schedule?.job_id !== target.job || schedule.occurrence_id !== target.occurrence || schedule.revision !== target.revision ||
          !Number.isSafeInteger(result.cursor) || result.cursor < 0 ||
          result.steps.some((step) => step.run_id !== target.run || step.session_id !== target.session))
        throw new Error("服务、运行或会话归属已变化，请返回定时任务重新选择。");
      setSnapshot(result);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setBusy(false); }
  };
  useEffect(() => {
    void load();
    const off = ipc.onSideCar((event) => {
      ++epoch.current; if (!editing.current) setSnapshot(null); setBusy(false);
      if (event.status === "started" && !editing.current && ipc.agentBaseUrl === target.host &&
          useSessionStore.getState().currentProjectId === target.project &&
          useSessionStore.getState().currentSessionId === target.session) {
        setError("");
        void load();
      } else setError("连接已变化，请重新读取目标运行；恢复交接会保留，状态变化时需重新核对。");
    });
    const unsubscribe = useSessionStore.subscribe((state) => {
      if (state.currentProjectId !== target.project || state.currentSessionId !== target.session) {
        ++epoch.current; setSnapshot(null); setBusy(false); setError("当前项目或会话已变化，请返回原任务重新查看。");
      }
    });
    return () => { ++epoch.current; off(); unsubscribe(); };
  }, [target]);
  return <div className="space-y-2 p-3 text-xs" data-testid="target-run-timeline">
    <h3 className="font-semibold">定时运行时间线</h3>
    <p className="break-all">运行：{target.run} · 发生：{target.occurrence}</p>
    <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void load()}>刷新此运行</Button>
      <Button type="button" size="sm" variant="ghost" onClick={onClear}>返回近期时间线</Button></div>
    {busy && <p role="status">正在核对并读取目标运行…</p>}
    {error && <p role="alert" className="break-words text-status-warning">{error}</p>}
    {(!!error || (snapshot && !supportsTargetRecovery(snapshot))) && <RetainedTargetRecovery host={target.host} service={target.service} project={target.project} session={target.session} run={target.run} />}
    {snapshot && <fieldset disabled={busy || !!error} hidden={busy || !!error} className="space-y-2"><article className="space-y-2"><p className="break-words font-medium">{snapshot.run.title || "未命名运行"} · {snapshot.run.status}</p>
      <p>读取时的步骤快照；运行状态和结果可能变化。较早运行可能不在近期时间线中。</p>
      {snapshot.run.error && <p className="break-words text-status-warning">{snapshot.run.error}</p>}
      {!snapshot.steps.length ? <p>暂无已记录步骤。</p> : <ol className="space-y-1.5">{[...snapshot.steps].sort((a, b) => a.ordinal - b.ordinal || a.id.localeCompare(b.id)).map((step) => <TimelineStep key={step.id} step={step} runStatus={snapshot.run.status} />)}</ol>}
      <RunAttachments run={snapshot.run} steps={snapshot.steps} reviewScope={{ sessionId: target.session, projectId: target.project, serviceId: target.service }} verifyRun />
      <details><summary>登记关联的文本成果</summary><RunTextArtifacts runId={target.run} sessionId={target.session} projectId={target.project} serviceId={target.service} review /></details>
      <TargetRecoveryEvidence snapshot={snapshot} host={target.host} service={target.service} project={target.project} session={target.session} refresh={load} onEditingChange={(value) => { editing.current = value; }} />
    </article></fieldset>}
  </div>;
}

function supportsTargetRecovery(snapshot: RunStepsResult): boolean {
  const run = snapshot.run;
  return ["interrupted", "failed", "cancelled", "budget_paused"].includes(run.status) ||
    (run.status === "completed" && run.metadata?.engine !== "codex" && run.metadata?.truncated === true);
}

function RetainedTargetRecovery({ host, service, project, session, run }: { host: string; service: string; project: string; session: string; run: string }): JSX.Element | null {
  const key = recoveryDraftKey(host, service, project, session, run), [, setRevision] = useState(0);
  const [verified, setVerified] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setVerified(false); setError(""); return () => { ++epoch.current; }; }, [key]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setVerified(false); setBusy(false); setError("连接变化，请重新核对服务。"); }), []);
  useEffect(() => useSessionStore.subscribe((state) => {
    if (state.currentProjectId !== project || state.currentSessionId !== session || ipc.agentBaseUrl !== host) {
      ++epoch.current; setVerified(false); setBusy(false);
    }
  }), [host, project, session]);
  const draft = readRetainedRecovery(key);
  if (!draft) return null;
  const verify = async () => {
    const ticket = ++epoch.current;
    setBusy(true); setError(""); setVerified(false);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (ticket !== epoch.current || ipc.agentBaseUrl !== host || useSessionStore.getState().currentProjectId !== project ||
          useSessionStore.getState().currentSessionId !== session) return;
      if (!service || identity.service_id !== service) throw new Error("当前服务与原服务身份不一致。");
      setVerified(true);
    } catch (reason) { if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-1 rounded border border-status-warning/30 p-2 text-xs">
    <p>此运行有页面内保留的未提交恢复编辑；核对原服务后可查看并复制。</p>
    <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void verify()}>核对服务并查看编辑</Button>
    {error && <p role="alert">{error}</p>}
    {verified && <><textarea aria-label="保留的恢复交接" readOnly className="block w-full rounded border border-line bg-surface-0 p-1" value={draft.handoff} />
      <p>预算编辑：{draft.changeBudget ? draft.budgetTokens || "未填写" : "未启用"}</p></>}
    <Button type="button" size="sm" variant="ghost" onClick={() => { if (window.confirm("放弃此运行保留的恢复编辑？")) { discardRetainedRecovery(key); setRevision((value) => value + 1); } }}>放弃恢复编辑</Button>
  </div>;
}

function TargetRecoveryEvidence({ snapshot, host, service, project, session, refresh, onEditingChange }: {
  snapshot: RunStepsResult; host: string; service: string; project: string; session: string; refresh: () => Promise<void>; onEditingChange: (editing: boolean) => void;
}): JSX.Element {
  const run = snapshot.run;
  const recoverable = supportsTargetRecovery(snapshot);
  return <section className="space-y-2 border-t border-line pt-2" aria-label="运行验证和恢复">
    <h4 className="font-medium">验证、预算与后续操作</h4>
    <VerificationBadge evidence={run.metadata?.verification as VerificationEvidence | undefined} />
    <VerificationDetails evidence={run.metadata?.verification as VerificationEvidence | undefined} />
    {run.status === "budget_paused" && <ProjectRunBudget host={host} service={service} project={project} session={session} run={run.id} />}
    {recoverable && <RunRecoveryActions run={run} refresh={refresh} expectedService={service} onEditingChange={onEditingChange} />}
    <p>只显示已保存记录；未知费用不是零，恢复需重新核对记录与副作用，并遵循原预算和权限。</p>
  </section>;
}

function TimelineStep({ step, runStatus }: { step: AgentRunStep; runStatus: string }): JSX.Element {
  const terminal = step.tool_name === "terminal" && step.payload?.source === "terminal";
  const Icon = iconForKind(step.kind);
  const statusClass = statusTone(step.status);
  const detailText = buildStepDetailText(step);
  const collapsedDetail = detailText ? summarizeStepText(detailText, step.kind) : "";
  if (step.payload?.source === "codex_steer") {
    return <li className="rounded border border-line p-2 text-xs"><p>运行中补充：{step.status === "completed" ? "Codex 已接受，不保证模型已处理" : "发送结果未确认；请核对任务，不会自动重发"}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{String(step.payload.text ?? "")}</pre></li>;
  }
  if (step.payload?.source === "native_steer") {
    return <li className="rounded border border-line p-2 text-xs"><p>运行中补充：{step.status === "pending" ? "已接收，等待下一轮边界" : step.status === "completed" ? "已加入后续上下文，不保证模型已处理" : "未确认生效，请核对"}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{String(step.payload.text ?? "")}</pre></li>;
  }
  return (
    <li
      data-testid="run-timeline-step"
      className="grid grid-cols-[18px_1fr] gap-2 rounded border border-transparent py-1"
    >
      <span className={"mt-0.5 flex h-[18px] w-[18px] items-center justify-center " + statusClass}>
        {step.status === "running" ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <Icon size={13} />
        )}
      </span>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[11px] font-medium text-minimax-fg">
            {step.title || labelForKind(step.kind)}
          </span>
          <span className={"shrink-0 text-[10px] " + statusClass}>{step.tool_name === "codex.webSearch" && step.status === "completed" ? "事件已结束" : ({ pending: "待处理", running: "进行中", completed: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[step.status] ?? step.status}</span>
          {step.duration_ms != null && (
            <span className="shrink-0 font-mono text-[11px] text-minimax-muted">
              {formatDuration(step.duration_ms)}
            </span>
          )}
        </div>
        {terminal ? <TerminalEvidence step={step} runStatus={runStatus} /> : detailText && (
          <ExpandablePre
            text={detailText}
            collapsedText={collapsedDetail}
            className="mt-0.5 text-minimax-muted"
            testId="run-timeline-step-detail"
          />
        )}
        <CodexMcpEvidence step={step} />
        <CodexWebSearchEvidence step={step} />
        <CapturedToolOutput step={step} />
      </div>
    </li>
  );
}

function CodexWebSearchEvidence({ step }: { step: AgentRunStep }): JSX.Element | null {
  if (step.tool_name !== "codex.webSearch") return null;
  const result = step.payload?.result as Record<string, unknown> | undefined;
  return <details className="mt-1 text-[11px] text-minimax-muted">
    <summary>查看 Codex 网页搜索事件</summary>
    <p>引擎报告的事件；结束不表示搜索成功或内容已核实。未记录搜索结果。</p>
    {result && <>
      <p>查询{result.truncated === true ? "（预览可能已截断）" : ""}：</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{typeof result.query === "string" && result.query ? result.query : "未提供"}</pre>
      <p>动作：</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{result.action_available === true ? String(result.action_preview ?? "") : "未提供"}</pre>
    </>}
    {!result && <p>等待结束事件；未收到结束事件时结果未知。</p>}
  </details>;
}

function CodexMcpEvidence({ step }: { step: AgentRunStep }): JSX.Element | null {
  if (step.tool_name !== "codex.mcpToolCall") return null;
  const args = step.payload?.args as Record<string, unknown> | undefined;
  const result = step.payload?.result as Record<string, unknown> | undefined;
  if (!args && !result) return null;
  return <details className="mt-1 text-[11px] text-minimax-muted">
    <summary>查看 Codex MCP 调用详情</summary>
    {args && <>
      <p>服务：{String(args.server ?? "未知")} · 工具：{String(args.tool ?? "未知")}</p>
      <p>参数{args.truncated === true ? "（预览已截断）" : ""}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{String(args.arguments_preview ?? "未提供")}</pre>
    </>}
    {result && <>
      <p>引擎状态：{String(result.status ?? "未知")} · {result.result_available === true ? "结果预览" : "未提供结果"}{result.truncated === true ? "（已截断）" : ""}</p>
      {result.result_available === true && <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{String(result.result_preview ?? "")}</pre>}
    </>}
  </details>;
}

interface OutputReference { output_ref: string; service_id: string; run_id: string; session_id: string; project_id: string | null; chars: number }
interface OutputPage { id: string; service_id: string; run_id: string; session_id: string; project_id: string | null; offset: number; next_offset: number; chars: number; page: string }

function CapturedToolOutput({ step }: { step: AgentRunStep }): JSX.Element | null {
  const session = useSessionStore((s) => s.currentSessionId);
  const project = useSessionStore((s) => s.sessions.find((item) => item.id === session)?.project_id ?? null);
  const selectedProject = useSessionStore((s) => s.currentProjectId);
  const [page, setPage] = useState<OutputPage | null>(null);
  const [error, setError] = useState("");
  const [cleared, setCleared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [match, setMatch] = useState<{ query: string; offset: number; next: number; text: string } | null>(null);
  const epoch = useRef(0);
  const meta = step.payload?.metadata as Record<string, unknown> | undefined;
  const ref = meta?.tool_output as OutputReference | undefined;
  useEffect(() => {
    setPage(null); setMatch(null); setQuery(""); setError(""); setBusy(false); setCleared(false);
    const version = epoch;
    return () => { version.current++; };
  }, [session, project, selectedProject, step.id]);
  useEffect(() => {
    if (!ref?.output_ref) return;
    return ipc.onSideCar(() => { epoch.current++; setPage(null); setMatch(null); setBusy(false); setError("连接变化，请重新读取输出。"); });
  }, [ref?.output_ref]);
  useEffect(() => ipc.on<{ run_id: string; session_id: string; output_ref: string; service_id: string }>(StreamEvent.RunOutputCleared, (event) => {
    const item = event.data;
    if (item && ref && item.output_ref === ref.output_ref && item.run_id === ref.run_id &&
        item.session_id === ref.session_id && item.service_id === ref.service_id) {
      epoch.current++; setPage(null); setMatch(null); setBusy(false); setCleared(true); setError("");
    }
  }), [ref]);
  if (!ref || typeof ref.output_ref !== "string" || ref.run_id !== step.run_id || ref.session_id !== session || ref.project_id !== project ||
      (selectedProject !== project && !(project === null && selectedProject === "inbox"))) return null;
  const load = async (offset: number) => {
    const ticket = ++epoch.current, host = ipc.agentBaseUrl;
    setBusy(true); setError("");
    try {
      const data = await ipc.request<OutputPage>("run.output_page", { run_id: ref.run_id, session_id: ref.session_id,
        output_ref: ref.output_ref, expected_service_id: ref.service_id, offset, limit: 4000 });
      const state = useSessionStore.getState();
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl || state.currentSessionId !== session ||
          (state.sessions.find((item) => item.id === session)?.project_id ?? null) !== project || state.currentProjectId !== selectedProject) return;
      if (data.id !== ref.output_ref || data.run_id !== ref.run_id || data.session_id !== ref.session_id ||
          data.project_id !== ref.project_id || data.service_id !== ref.service_id || data.offset !== offset) throw new Error("输出归属已变化");
      setPage(data);
    } catch (failure) { if (ticket === epoch.current) { setPage(null); if (String(failure).includes("explicitly cleared")) { setCleared(true); setError(""); } else setError(String(failure)); } }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const find = async (offset: number) => {
    if (!query || query.length > 256) return;
    const ticket = ++epoch.current, host = ipc.agentBaseUrl, term = query;
    setBusy(true); setError(""); setMatch(null);
    try {
      const result = await ipc.request<{ id: string; run_id: string; session_id: string; service_id: string; query: string;
        found: boolean; offset: number; match_offset?: number; next_search_offset?: number; page?: string }>("run.output_page", {
        run_id: ref.run_id, session_id: ref.session_id, output_ref: ref.output_ref,
        expected_service_id: ref.service_id, offset, limit: 4000, query: term });
      const state = useSessionStore.getState();
      if (ticket !== epoch.current || host !== ipc.agentBaseUrl || state.currentSessionId !== session ||
          (state.sessions.find((item) => item.id === session)?.project_id ?? null) !== project || state.currentProjectId !== selectedProject) return;
      if (result.id !== ref.output_ref || result.run_id !== ref.run_id || result.session_id !== ref.session_id ||
          result.service_id !== ref.service_id || result.query !== term) throw new Error("输出归属已变化");
      if (!result.found) { setError("此位置之后没有匹配内容。"); return; }
      if (typeof result.page !== "string" || !Number.isSafeInteger(result.match_offset) || !Number.isSafeInteger(result.next_search_offset)) throw new Error("搜索结果不可用");
      setMatch({ query: term, offset: result.match_offset!, next: result.next_search_offset!, text: result.page });
    } catch (failure) { if (ticket === epoch.current) setError(String(failure)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="mt-1 text-[11px] text-minimax-muted">
    <button type="button" disabled={busy || cleared} onClick={() => void load(0)}>查看已捕获输出</button>
    {Number.isSafeInteger(ref.chars) && ref.chars >= 0 && <>{" "}<button type="button" disabled={busy || cleared} onClick={() => void load(Math.max(0, ref.chars - 4000))}>查看末尾</button></>}
    {cleared && <p>已清理，无法再读取。</p>}
    <p>仅包含工具实际捕获内容；{meta?.truncated_stdout === true || meta?.truncated_stderr === true ? "命令输出在捕获时已截断。" : "请核对原始截断标记。"}</p>
    <label>查找区分大小写的原文<input aria-label="查找工具输出" value={query} maxLength={256} disabled={busy || cleared} onChange={(event) => { setQuery(event.target.value); setMatch(null); }} className="ml-1 rounded border border-line bg-surface-0 px-1" /></label>{" "}
    <button type="button" disabled={busy || cleared || !query} onClick={() => void find(0)}>查找</button>{" "}
    {match && match.query === query && <button type="button" disabled={busy || cleared} onClick={() => void find(match.next)}>查找下一处</button>}
    {match && <div><p>匹配字符位置 {match.offset}；以下为附近摘录。</p><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{match.text}</pre></div>}
    {error && <p role="alert">{error}</p>}
    {page && <><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all">{page.page}</pre>
      <div>{page.offset}–{page.next_offset} / {page.chars} 字符</div>
      <button type="button" disabled={busy || page.offset === 0} onClick={() => void load(Math.max(0, page.offset - 4000))}>上一页</button>{" "}
      <button type="button" disabled={busy || page.next_offset >= page.chars} onClick={() => void load(page.next_offset)}>下一页</button></>}
  </div>;
}

function TerminalEvidence({ step, runStatus }: { step: AgentRunStep; runStatus: string }): JSX.Element {
  const payload = step.payload ?? {};
  const tail = typeof payload.output_tail === "string" ? payload.output_tail : null;
  const truncated = payload.output_truncated;
  return (
    <div className="mt-1 text-[11px] text-minimax-muted" data-testid="terminal-evidence">
      <div>命令：{typeof payload.command === "string" ? payload.command : "未知"}</div>
      <div>目录：{typeof payload.cwd === "string" ? payload.cwd : "未知"}</div>
      <div>运行状态：{runStatus}；退出码：{typeof payload.exit_code === "number" ? payload.exit_code : "未知"}</div>
      {runStatus === "interrupted" && <div>运行中断，命令的副作用未知，请检查工作区。</div>}
      {payload.output_may_be_incomplete === true && <div>检查点之后的输出可能丢失或不完整。</div>}
      {step.error && <div>错误：{step.error}</div>}
      {tail === null ? <div>没有已保存的输出；中断前的输出可能未持久化。</div> : <>
        <div>已保存的输出尾部（最多 1200 字符；不是可恢复的终端会话）。
          {truncated === true ? "输出已截断。" : truncated === false ? "记录时未截断。" : "截断情况未知。"}
        </div>
        {tail === "" ? <div>保存的输出为空。</div> : <pre data-testid="terminal-output-tail" className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono">{tail}</pre>}
      </>}
    </div>
  );
}

function ExpandablePre({
  text,
  collapsedText,
  className = "",
  testId,
}: {
  text: string;
  collapsedText?: string;
  className?: string;
  testId?: string;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const trimmed = text.trim();
  const preview = (collapsedText ?? summarizeLongText(trimmed, 320)).trim();
  const canExpand = preview !== trimmed;
  const visibleText = expanded || !canExpand ? trimmed : preview;
  const scrollClass = expanded ? "max-h-72 overflow-auto" : "max-h-20 overflow-hidden";

  return (
    <div className={className}>
      <pre
        data-testid={testId}
        className={`${scrollClass} whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed`}
      >
        {visibleText}
      </pre>
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 rounded border border-minimax-border px-1.5 py-0.5 text-[11px] text-minimax-muted transition-colors hover:border-minimax-accent/40 hover:text-minimax-fg"
        >
          {expanded ? strings.rightPanel.timeline.showLess : strings.rightPanel.timeline.showFull}
        </button>
      )}
    </div>
  );
}

function buildStepDetailText(step: AgentRunStep): string {
  const summary = step.summary?.trim();
  const error = step.error?.trim();
  const parts = [summary];
  if (error && error !== summary) {
    parts.push(strings.rightPanel.timeline.errorPrefix(error));
  }
  return parts.filter(Boolean).join("\n\n");
}

function summarizeStepText(text: string, kind: AgentRunStepKind): string {
  const withoutCode = text.replace(
    /```[\s\S]*?```/g,
    strings.rightPanel.timeline.codeBlock,
  );
  const compact = withoutCode.replace(/\n{3,}/g, "\n\n").trim();
  const limit = kind === "final" ? 180 : 260;
  return summarizeLongText(compact, limit);
}

function summarizeArgs(args: Record<string, unknown>): string {
  return summarizeLongText(formatArgs(args), 360);
}

function summarizeLongText(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function RunStatusBadge({ status }: { status: string }): JSX.Element {
  const cls =
    status === "completed"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
      : status === "failed" || status === "cancelled" || status === "interrupted"
        ? "border-red-500/30 bg-red-500/10 text-status-error"
        : ["awaiting_approval", "waiting_input", "budget_paused"].includes(status)
          ? "border-status-warning/30 bg-surface-1 text-status-warning"
          : "border-minimax-accent/30 bg-minimax-accent/10 text-minimax-accent";
  return (
    <span className={"shrink-0 rounded border px-1.5 py-0.5 text-[11px] " + cls}>
      {({ queued: "排队中", planning: "规划中", running: "运行中", awaiting_approval: "等待授权", waiting_input: "等待回答", budget_paused: "预算暂停", completed: "已结束", failed: "失败", cancelled: "已取消", interrupted: "中断，待核对" } as Record<string, string>)[status] ?? `未知状态：${status}`}
    </span>
  );
}

function iconForKind(kind: AgentRunStepKind) {
  switch (kind) {
    case "thought":
      return Clock3;
    case "tool_call":
      return TerminalSquare;
    case "observation":
      return MessageSquareText;
    case "approval":
      return ShieldQuestion;
    case "patch":
      return FileCode2;
    case "final":
      return CheckCircle2;
    default:
      return Circle;
  }
}

function labelForKind(kind: AgentRunStepKind): string {
  return strings.rightPanel.timeline.stepKinds[kind];
}

function statusTone(status: AgentRunStepStatus): string {
  if (status === "completed") return "text-emerald-300";
  if (status === "failed" || status === "cancelled") return "text-status-error";
  if (status === "running") return "text-minimax-accent";
  return "text-minimax-muted";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatArgs(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}
