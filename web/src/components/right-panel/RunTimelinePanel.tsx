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
import type { AgentRunStep, AgentRunStepKind, AgentRunStepStatus } from "../../types/ipc";
import { buildPermissionPatchFiles } from "../../lib/permissionPatchPreview";
import { PermissionPatchPreview } from "../modals/PermissionPatchPreview";
import { strings } from "../../ui/strings";
import { Button } from "../../ui";
import { RunRecoveryActions } from "./RunRecoveryActions";
import { RunBudgetPanel } from "./RunBudgetPanel";
import { VerificationBadge } from "../chat/VerificationBadge";
import { VerificationDetails } from "../chat/VerificationDetails";
import { RunWorkspaceDiff } from "./RunWorkspaceDiff";
import type { VerificationEvidence } from "../../types/ipc";

export interface RunTimelinePanelProps {
  testId?: string;
}

export function RunTimelinePanel({
  testId = "run-timeline-panel",
}: RunTimelinePanelProps): JSX.Element {
  const init = useRunTimelineStore((s) => s.init);
  const loadForSession = useRunTimelineStore((s) => s.loadForSession);
  const loading = useRunTimelineStore((s) => s.loading);
  const error = useRunTimelineStore((s) => s.error);
  const runs = useRunTimelineStore((s) => s.runs);
  const order = useRunTimelineStore((s) => s.order);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
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
    () => order.map((id) => runs[id]).filter((r) => r && (!currentSessionId || r.session_id === currentSessionId)),
    [order, runs, currentSessionId],
  );

  const pending = Object.values(pendingPermissions).sort((a, b) => a.received_at - b.received_at);
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
    <div role="alert" className="px-3 py-2 text-xs text-status-warning">
      <p>{strings.rightPanel.timeline.unavailable} {error}</p>
      {visibleRuns.length > 0 && <p>{strings.rightPanel.timeline.stale}</p>}
      <Button size="sm" variant="ghost" loading={loading}
        onClick={() => void loadForSession(currentSessionId)}>{strings.common.retry}</Button>
    </div>
  ) : null;

  if (visibleRuns.length === 0 && pending.length === 0) {
    return (
      <div data-testid={testId} className="px-3 py-4 text-xs text-minimax-muted">
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
      {syncError}
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
                  <span className="truncate">{request.tool}</span>
                </div>
                <ExpandablePre
                  text={formatArgs(request.args)}
                  collapsedText={summarizeArgs(request.args)}
                  className="mt-1 text-amber-100/80"
                  testId="run-timeline-approval-args"
                />
                <PermissionPatchPreview
                  files={patchFiles}
                  testId="run-timeline-approval-patch-preview"
                />
                <div className="mt-2 flex justify-end gap-1.5">
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
          <article key={run.id} className="px-3 py-2.5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-minimax-fg">
                  {run.title || strings.rightPanel.timeline.runFallbackTitle}
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-minimax-muted">
                  {run.id.slice(0, 12)}
                </div>
              </div>
              <RunStatusBadge status={run.status} />
            </div>
            {!["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) && <VerificationBadge evidence={run.metadata?.verification as VerificationEvidence | undefined} />}
            {!["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(run.status) && <VerificationDetails evidence={run.metadata?.verification as VerificationEvidence | undefined} />}
            {run.queuedInput && <details className="my-2 text-xs"><summary>已接收、尚未开始的任务内容</summary>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{run.queuedInput.text}</pre>
              {run.queuedInput.truncated && <p>内容预览已截断。</p>}
            </details>}
            <ol className="space-y-1.5">
              {run.steps.map((step) => (
                <TimelineStep key={step.id} step={step} runStatus={run.status} />
              ))}
            </ol>
            <RunBudgetPanel run={run} />
            <RunWorkspaceDiff run={run} />
            <RunRecoveryActions run={run} refresh={() => loadForSession(currentSessionId)} />
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

function TimelineStep({ step, runStatus }: { step: AgentRunStep; runStatus: string }): JSX.Element {
  const terminal = step.tool_name === "terminal" && step.payload?.source === "terminal";
  const Icon = iconForKind(step.kind);
  const statusClass = statusTone(step.status);
  const detailText = buildStepDetailText(step);
  const collapsedDetail = detailText ? summarizeStepText(detailText, step.kind) : "";
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
      </div>
    </li>
  );
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
        : "border-minimax-accent/30 bg-minimax-accent/10 text-minimax-accent";
  return (
    <span className={"shrink-0 rounded border px-1.5 py-0.5 text-[11px] " + cls}>
      {({ queued: "排队中", interrupted: "中断，待核对", waiting_input: "等待输入", budget_paused: "预算暂停" } as Record<string, string>)[status] ?? status}
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
