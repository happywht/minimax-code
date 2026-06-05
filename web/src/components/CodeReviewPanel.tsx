/**
 * Code review panel — displays review results from the code-review skill.
 *
 * v0.3.1: embedded in RightPanel as a collapsible section. "Run Review"
 * triggers the store's ``runReview()`` which fetches a git diff and
 * invokes the ``code-review:code-review`` skill.
 */
import { AlertTriangle, FileCode, Loader2, Play, Trash2, XCircle, Info } from "lucide-react";
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

export function CodeReviewPanel({
  testId = "code-review-panel",
}: CodeReviewPanelProps): JSX.Element {
  const comments = useCodeReviewStore((s) => s.comments);
  const stats = useCodeReviewStore((s) => s.stats);
  const rawText = useCodeReviewStore((s) => s.rawText);
  const loading = useCodeReviewStore((s) => s.loading);
  const error = useCodeReviewStore((s) => s.error);
  const runReview = useCodeReviewStore((s) => s.runReview);
  const clear = useCodeReviewStore((s) => s.clear);

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
          {loading ? "Reviewing…" : "Run Review"}
        </button>
        {(comments.length > 0 || rawText) && (
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
      {stats && (
        <div
          data-testid="code-review-stats"
          className="mt-2 flex items-center gap-3 rounded-md border border-minimax-border bg-minimax-bg/40 px-2 py-1.5 text-[10px] text-minimax-muted"
        >
          <span className="flex items-center gap-1">
            <FileCode size={9} />
            {stats.files} file{stats.files !== 1 ? "s" : ""}
          </span>
          <span className="text-emerald-400">+{stats.additions}</span>
          <span className="text-red-400">−{stats.deletions}</span>
        </div>
      )}

      {/* Comments list */}
      {comments.length > 0 && (
        <ul data-testid="code-review-comments" className="mt-2 space-y-1.5">
          {comments.map((c, i) => {
            const badge = SEVERITY_BADGE[c.severity] ?? SEVERITY_BADGE.info;
            return (
              <li
                key={i}
                data-testid={`code-review-comment-${i}`}
                className="rounded-md border border-minimax-border bg-minimax-bg/40 p-2"
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className={`inline-flex items-center gap-0.5 rounded border px-1 py-0.5 text-[9px] font-medium ${badge.cls}`}
                  >
                    {badge.icon}
                    {c.severity}
                  </span>
                  <span className="truncate font-mono text-[10px] text-minimax-muted">
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
      {!comments.length && rawText && (
        <pre
          data-testid="code-review-raw"
          className="mt-2 max-h-48 overflow-auto rounded-md border border-minimax-border bg-minimax-bg/40 p-2 text-[10px] text-minimax-muted whitespace-pre-wrap"
        >
          {rawText}
        </pre>
      )}

      {/* Empty state (no review run yet) */}
      {!loading && !error && !comments.length && !rawText && (
        <div className="mt-2 text-center text-[11px] italic text-minimax-muted">
          Click "Run Review" to analyze recent changes.
        </div>
      )}
    </div>
  );
}
