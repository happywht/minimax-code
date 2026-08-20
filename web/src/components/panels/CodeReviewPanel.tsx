/**
 * Code review panel — multi-dimensional review UI.
 *
 * v0.3.1: embedded in RightPanel as a collapsible section.
 * v0.8.0: multi-dimensional tabs — Overview | Security | Performance | Style.
 * Each dimension invokes its own tool. "Run All" triggers all checks.
 */

import { useState } from "react";
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
import { Badge, Button, IconButton, type BadgeTone } from "../../ui";
import { strings } from "../../ui/strings";

export interface CodeReviewPanelProps {
  testId?: string;
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

  // Select comments for active dimension
  const displayComments =
    activeTab === "overview"
      ? comments
      : dimensions[activeTab]?.comments ?? [];

  const displayStats =
    activeTab === "overview"
      ? stats
      : dimensions[activeTab]?.stats ?? null;

  return (
    <div data-testid={testId} className="px-3 pb-3">
      {/* Action bar */}
      <div className="flex items-center gap-1.5">
        <Button
          variant="subtle"
          size="sm"
          data-testid="code-review-run-btn"
          onClick={() => void runReview()}
          loading={loading}
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
          disabled={loading}
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
      {!displayComments.length && rawText && activeTab === "overview" && (
        <pre
          data-testid="code-review-raw"
          className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-2/40 p-2 text-[11px] text-ink-1"
        >
          {rawText}
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
