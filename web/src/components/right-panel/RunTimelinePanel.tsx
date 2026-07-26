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

  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
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

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceFromBottom <= 50;
    setIsFollowing(nearBottom);
    if (nearBottom) setHasNewEvents(false);
  };

  if (visibleRuns.length === 0 && pending.length === 0) {
    return (
      <div data-testid={testId} className="px-3 py-4 text-xs text-minimax-muted">
        {loading ? "Loading runs..." : error ? "Run history unavailable." : "No active runs."}
      </div>
    );
  }

  return (
    <div data-testid={testId} className="relative">
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
                    Deny
                  </button>
                  <button
                    type="button"
                    onClick={() => void resolvePermission(request.request_id, "allow")}
                    className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/25"
                  >
                    Allow
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
                  {run.title || "Agent run"}
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-minimax-muted">
                  {run.id.slice(0, 12)}
                </div>
              </div>
              <RunStatusBadge status={run.status} />
            </div>
            <ol className="space-y-1.5">
              {run.steps.map((step) => (
                <TimelineStep key={step.id} step={step} />
              ))}
            </ol>
          </article>
        ))}
      </div>
      {hasNewEvents && (
        <button
          type="button"
          data-testid="run-timeline-new-events"
          onClick={scrollToBottom}
          className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full border border-minimax-border bg-minimax-panel/95 px-2.5 py-1 text-[11px] font-medium text-minimax-fg shadow-lg backdrop-blur transition-colors duration-200 hover:bg-minimax-border"
        >
          New events ↓
        </button>
      )}
    </div>
  );
}

function TimelineStep({ step }: { step: AgentRunStep }): JSX.Element {
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
        {detailText && (
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
          {expanded ? "Show less" : "Show full"}
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
    parts.push(`Error: ${error}`);
  }
  return parts.filter(Boolean).join("\n\n");
}

function summarizeStepText(text: string, kind: AgentRunStepKind): string {
  const withoutCode = text.replace(/```[\s\S]*?```/g, "[code block]");
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
      : status === "failed" || status === "cancelled"
        ? "border-red-500/30 bg-red-500/10 text-status-error"
        : "border-minimax-accent/30 bg-minimax-accent/10 text-minimax-accent";
  return (
    <span className={"shrink-0 rounded border px-1.5 py-0.5 text-[11px] " + cls}>
      {status}
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
  return kind.replace("_", " ");
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
