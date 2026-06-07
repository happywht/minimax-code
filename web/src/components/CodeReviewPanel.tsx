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
  Loader2,
  Play,
  ShieldAlert,
  Trash2,
  Zap,
  XCircle,
} from "lucide-react";
import { useCodeReviewStore } from "../stores/codeReviewStore";

export interface CodeReviewPanelProps {
  testId?: string;
}

const SEVERITY_BADGE: Record<string, { icon: JSX.Element; cls: string }> = {
  info: { icon: <Info size={10} />, cls: "bg-blue-500/10 text-blue-300 border-blue-500/30" },
  warning: {
    icon: <AlertTriangle size={10} />,
    cls: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  },
  error: { icon: <XCircle size={10} />, cls: "bg-red-500/10 text-red-300 border-red-500/30" },
};

type DimensionTab = "overview" | "security" | "performance" | "style";

const TABS: { key: DimensionTab; label: string; icon: JSX.Element }[] = [
  { key: "overview", label: "Overview", icon: <FileCode size={9} /> },
  { key: "security", label: "Security", icon: <ShieldAlert size={9} /> },
  { key: "performance", label: "Perf", icon: <Zap size={9} /> },
  { key: "style", label: "Style", icon: <AlertTriangle size={9} /> },
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
        <button
          type="button"
          data-testid="code-review-run-btn"
          onClick={() => void runReview()}
          disabled={loading}
          className="flex items-center gap-1 rounded-md bg-minimax-accent/10 border border-minimax-accent/30 px-2 py-1 text-[11px] text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-40"
        >
          {loading ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
          {loading ? "Reviewing…" : "Review Diff"}
        </button>
        <button
          type="button"
          data-testid="code-review-run-all-btn"
          onClick={() => void runAllChecks()}
          disabled={loading}
          className="flex items-center gap-1 rounded-md bg-minimax-border/40 border border-minimax-border px-2 py-1 text-[11px] text-minimax-fg hover:bg-minimax-border disabled:opacity-40"
          title="Run all dimension checks"
        >
          <Play size={10} />
          Run All
        </button>
        {(comments.length > 0 || rawText || Object.keys(dimensions).length > 0) && (
          <button
            type="button"
            onClick={clear}
            className="rounded p-1 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            aria-label="Clear review"
          >
            <Trash2 size={10} />
          </button>
        )}
      </div>

      {/* Dimension tabs */}
      <div className="mt-2 flex gap-0.5 border-b border-minimax-border">
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
                "flex items-center gap-1 border-b-2 px-2 py-1 text-[11px] font-medium transition-colors " +
                (isActive
                  ? "border-minimax-accent text-minimax-accent"
                  : "border-transparent text-minimax-muted hover:text-minimax-fg")
              }
            >
              {tab.icon}
              {tab.label}
              {hasFindings && (
                <span className="ml-0.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
              )}
            </button>
          );
        })}
      </div>

      {/* Error */}
      {error && (
        <div
          data-testid="code-review-error"
          className="mt-2 rounded-md bg-red-500/10 px-2 py-1.5 text-[11px] text-red-300"
        >
          {error}
        </div>
      )}

      {/* Stats banner */}
      {displayStats && (
        <div
          data-testid="code-review-stats"
          className="mt-2 flex items-center gap-3 rounded-md border border-minimax-border bg-minimax-bg/40 px-2 py-1.5 text-[11px] text-minimax-muted"
        >
          <span className="flex items-center gap-1">
            <FileCode size={9} />
            {displayStats.files} file{displayStats.files !== 1 ? "s" : ""}
          </span>
          <span className="text-emerald-400">+{displayStats.additions}</span>
          <span className="text-red-400">−{displayStats.deletions}</span>
        </div>
      )}

      {/* Comments list */}
      {displayComments.length > 0 && (
        <ul data-testid="code-review-comments" className="mt-2 space-y-1.5">
          {displayComments.map((c, i) => {
            const badge = SEVERITY_BADGE[c.severity] ?? SEVERITY_BADGE.info;
            return (
              <li
                key={i}
                data-testid={`code-review-comment-${i}`}
                className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className={`inline-flex items-center gap-0.5 rounded border px-1 py-0.5 text-[11px] font-medium ${badge.cls}`}
                  >
                    {badge.icon}
                    {c.severity}
                  </span>
                  <span className="truncate font-mono text-[11px] text-minimax-muted">
                    {c.file}{c.line != null ? `:${c.line}` : ""}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-minimax-fg">
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
          className="mt-2 max-h-48 overflow-auto rounded-md border border-minimax-border bg-minimax-bg/40 p-2 text-[11px] text-minimax-muted whitespace-pre-wrap"
        >
          {rawText}
        </pre>
      )}

      {/* Empty state */}
      {!loading && !error && !displayComments.length && !rawText && !Object.keys(dimensions).length && (
        <div className="mt-2 text-center text-[11px] italic text-minimax-muted">
          Click "Review Diff" or "Run All" to analyze code.
        </div>
      )}
    </div>
  );
}
