/**
 * Code review panel — multi-dimensional review UI.
 *
 * v0.3.1: embedded in RightPanel as a collapsible section.
 * v0.8.0: multi-dimensional tabs — Overview | Security | Performance | Style.
 * Each dimension invokes its own tool. "Run All" triggers all checks.
 */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  FileCode,
  Info,
  Play,
  ShieldAlert,
  Trash2,
  Zap,
  XCircle,
} from "lucide-react";
import { useCodeReviewStore } from "../../stores/codeReviewStore";
import { useSessionStore } from "../../stores/sessionStore";
import { Badge, Button, IconButton, type BadgeTone } from "../../ui";
import { strings } from "../../ui/strings";

export interface CodeReviewPanelProps {
  testId?: string;
  onOpenRun?: () => void;
}

const SEVERITY_DISPLAY: Record<string, { icon: JSX.Element; tone: BadgeTone }> = {
  info: { icon: <Info size={10} />, tone: "info" },
  warning: { icon: <AlertTriangle size={10} />, tone: "warning" },
  error: { icon: <XCircle size={10} />, tone: "error" },
};

type DimensionTab = "overview" | "security" | "performance" | "style";

const TABS: { key: DimensionTab; label: string; icon: JSX.Element }[] = [
  { key: "overview", label: strings.panels.codeReview.tabOverview, icon: <FileCode size={9} /> },
  { key: "security", label: strings.panels.codeReview.tabSecurity, icon: <ShieldAlert size={9} /> },
  { key: "performance", label: strings.panels.codeReview.tabPerformance, icon: <Zap size={9} /> },
  { key: "style", label: strings.panels.codeReview.tabStyle, icon: <AlertTriangle size={9} /> },
];

export function CodeReviewPanel({
  testId = "code-review-panel",
  onOpenRun,
}: CodeReviewPanelProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<DimensionTab>("overview");

  const comments = useCodeReviewStore((s) => s.comments);
  const stats = useCodeReviewStore((s) => s.stats);
  const rawText = useCodeReviewStore((s) => s.rawText);
  const loading = useCodeReviewStore((s) => s.loading);
  const error = useCodeReviewStore((s) => s.error);
  const dimensions = useCodeReviewStore((s) => s.dimensions);
  const runReview = useCodeReviewStore((s) => s.runReview);
  const runAllChecks = useCodeReviewStore((s) => s.runAllChecks);
  const clear = useCodeReviewStore((s) => s.clear);
  const bindSession = useCodeReviewStore((s) => s.bindSession);
  const refresh = useCodeReviewStore((s) => s.refresh);
  const retryPending = useCodeReviewStore((s) => s.retryPending);
  const cancel = useCodeReviewStore((s) => s.cancel);
  const openRun = useCodeReviewStore((s) => s.openRun);
  const invocations = useCodeReviewStore((s) => s.invocations);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const project = useSessionStore((s) => s.projects.find((item) => item.id === session?.project_id));
  const native = Boolean(session && session.engine !== "codex");
  useEffect(() => { void bindSession(native ? currentSessionId : null); }, [bindSession, native, currentSessionId]);
  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [loading, refresh]);
  const invocation = invocations[activeTab];
  const dimension = dimensions[activeTab];
  const runActive = invocation && ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(invocation.status);

  // Select comments for active dimension
  const displayComments =
    activeTab === "overview"
      ? comments
      : dimensions[activeTab]?.comments ?? [];

  const displayStats =
    activeTab === "overview"
      ? stats
      : dimensions[activeTab]?.stats ?? null;

  // Dimension tabs get their own rawText outlet; the overview tab keeps
  // the top-level one. Without this, per-dimension review text had no
  // render path at all.
  const displayRawText =
    activeTab === "overview" ? rawText : dimensions[activeTab]?.rawText ?? "";

  return (
    <div data-testid={testId} className="px-3 pb-3">
      <div data-testid="code-review-binding" className="mb-2 break-all text-[11px] text-ink-2">
        {native ? `${session?.title} · ${session?.workspace_path ?? project?.root_path ?? "会话绑定工作区"}` : "请选择 Native 会话后审查"}
      </div>
      {/* Action bar */}
      <div className="flex items-center gap-1.5">
        <Button
          variant="subtle"
          size="sm"
          data-testid="code-review-run-btn"
          onClick={() => void runReview()}
          loading={loading}
          disabled={!native || loading}
          icon={<Play />}
          className="h-6 px-2 text-[11px]"
        >
          {loading ? strings.panels.codeReview.reviewing : strings.panels.codeReview.reviewDiff}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          data-testid="code-review-run-all-btn"
          onClick={() => void runAllChecks()}
          disabled={!native || loading}
          icon={<Play />}
          title={strings.panels.codeReview.runAllTitle}
          className="h-6 px-2 text-[11px]"
        >
          {strings.panels.codeReview.runAll}
        </Button>
        {(comments.length > 0 || rawText || Object.keys(dimensions).length > 0) && (
          <IconButton
            size="sm"
            onClick={clear}
            aria-label={strings.panels.codeReview.clearAria}
          >
            <Trash2 />
          </IconButton>
        )}
      </div>

      {(invocation || dimension?.status) && (
        <div data-testid="code-review-run-status" className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-1">
          <span>{invocation?.status ?? dimension?.status}{dimension?.partial ? " · 结果不完整，需核查" : ""}</span>
          {invocation?.status === "budget_paused" && <span>预算暂停，请在任务时间线核定责任或提高累计额度</span>}
          {invocation?.runId && <Button size="sm" variant="ghost" data-testid="code-review-open-run" onClick={() => void openRun(activeTab).then((opened) => { if (opened) onOpenRun?.(); })}>查看任务</Button>}
          {runActive && <Button size="sm" variant="danger" data-testid="code-review-cancel" onClick={() => void cancel(activeTab)}>取消</Button>}
          {invocation?.status === "admission_unknown" && <Button size="sm" variant="secondary" data-testid="code-review-retry" onClick={() => void retryPending(activeTab)}>使用原请求重试</Button>}
          <Button size="sm" variant="ghost" onClick={() => void refresh()}>刷新结果</Button>
          {dimension?.error && <span role="alert">{dimension.error}</span>}
        </div>
      )}

      {/* Dimension tabs */}
      <div className="mt-2 flex gap-0.5 border-b border-line">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          const hasFindings =
            tab.key === "overview"
              ? comments.length > 0
              : (dimensions[tab.key]?.comments?.length ?? 0) > 0;
          return (
            <button
              key={tab.key}
              type="button"
              data-testid={`code-review-tab-${tab.key}`}
              onClick={() => setActiveTab(tab.key)}
              className={
                "flex items-center gap-1 border-b-2 px-2 py-1 text-[11px] font-medium transition-colors duration-150 " +
                (isActive
                  ? "border-accent text-accent"
                  : "border-transparent text-ink-1 hover:text-ink-0")
              }
            >
              {tab.icon}
              {tab.label}
              {hasFindings && (
                <span className="ml-0.5 inline-block h-1.5 w-1.5 rounded-full bg-status-warning" />
              )}
            </button>
          );
        })}
      </div>

      {/* Error */}
      {error && (
        <div
          data-testid="code-review-error"
          className="mt-2 rounded-md border border-status-error/30 bg-[var(--status-error-subtle)] px-2 py-1.5 text-[11px] text-status-error"
        >
          {error}
        </div>
      )}

      {/* Stats banner */}
      {displayStats && (
        <div
          data-testid="code-review-stats"
          className="mt-2 flex items-center gap-3 rounded-md border border-line bg-surface-2/40 px-2 py-1.5 text-[11px] text-ink-1"
        >
          <span className="flex items-center gap-1">
            <FileCode size={9} />
            {strings.panels.fileCount(displayStats.files)}
          </span>
          <span className="text-status-success">+{displayStats.additions}</span>
          <span className="text-status-error">−{displayStats.deletions}</span>
        </div>
      )}

      {/* Comments list */}
      {displayComments.length > 0 && (
        <ul data-testid="code-review-comments" className="mt-2 space-y-1.5">
          {displayComments.map((c, i) => {
            const display = SEVERITY_DISPLAY[c.severity] ?? SEVERITY_DISPLAY.info;
            return (
              <li
                key={i}
                data-testid={`code-review-comment-${i}`}
                className="rounded-md border border-line bg-surface-2/40 p-2"
              >
                <div className="flex items-center gap-1.5">
                  <Badge tone={display.tone}>
                    {display.icon}
                    {c.severity}
                  </Badge>
                  <span className="truncate font-mono text-[11px] text-ink-2">
                    {c.file}{c.line != null ? `:${c.line}` : ""}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-0">
                  {c.message}
                </p>
              </li>
            );
          })}
        </ul>
      )}

      {/* Raw text fallback */}
      {!displayComments.length && displayRawText && (
        <pre
          data-testid="code-review-raw"
          className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-2/40 p-2 text-[11px] text-ink-1"
        >
          {displayRawText}
        </pre>
      )}

      {/* Empty state */}
      {!loading && !error && !displayComments.length && !rawText && !Object.keys(dimensions).length && (
        <div className="mt-2 text-center text-[11px] italic text-ink-2">
          {strings.panels.codeReview.emptyHint}
        </div>
      )}
    </div>
  );
}
