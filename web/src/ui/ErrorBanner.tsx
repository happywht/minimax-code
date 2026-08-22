import { AlertTriangle, RotateCw } from "lucide-react";
import { strings } from "./strings";

export interface ErrorBannerProps {
  /** Error detail — shown verbatim after the leading icon. */
  message: string;
  /** Renders a compact retry button when provided. */
  onRetry?: () => void;
  testId?: string;
}

/**
 * ErrorBanner — small inline error strip for panel-level load failures.
 *
 * Unlike a toast it stays visible until the next successful load (or a
 * manual retry), so an empty list below it is never mistaken for "no
 * data". Use for panel loads; keep toasts for one-shot actions
 * (create/delete/…) whose outcome speaks through the refreshed list.
 */
export function ErrorBanner({
  message,
  onRetry,
  testId = "error-banner",
}: ErrorBannerProps): JSX.Element {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="flex items-start gap-2 rounded-lg border border-status-error/30 bg-status-error/5 px-3 py-2 text-[11px] text-status-error"
    >
      <AlertTriangle size={13} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span className="min-w-0 flex-1 leading-4 break-all">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          data-testid={`${testId}-retry`}
          className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-medium transition-colors hover:bg-status-error/10"
        >
          <RotateCw size={11} aria-hidden="true" />
          {strings.common.retry}
        </button>
      )}
    </div>
  );
}
