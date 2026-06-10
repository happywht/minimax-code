import { AlertTriangle, Loader2, RefreshCw, WifiOff } from "lucide-react";

export type ConnectionBannerState = "connecting" | "disconnected" | "error";

export interface ConnectionBannerProps {
  state: ConnectionBannerState;
  retryDelayMs: number;
  onRetry: () => void | Promise<void>;
}

const COPY: Record<ConnectionBannerState, { title: string; detail: string }> = {
  connecting: {
    title: "Connecting to agent",
    detail: "Preparing the local runtime.",
  },
  disconnected: {
    title: "Agent disconnected",
    detail: "Auto-retry is running in the background.",
  },
  error: {
    title: "Connection error",
    detail: "The local agent did not respond.",
  },
};

export function ConnectionBanner({
  state,
  retryDelayMs,
  onRetry,
}: ConnectionBannerProps): JSX.Element {
  const copy = COPY[state];
  const retrySeconds = Math.max(1, Math.ceil(retryDelayMs / 1000));
  const isConnecting = state === "connecting";

  return (
    <div
      data-testid="connection-banner"
      className="pointer-events-auto fixed inset-x-3 top-3 z-40 mx-auto max-w-2xl rounded-lg border border-minimax-border bg-minimax-panel/95 px-3 py-2 text-xs shadow-2xl backdrop-blur animate-in slide-in-from-top-2 fade-in duration-200"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <div
          className={
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-md " +
            (state === "error" ? "bg-red-500/10 text-status-error" : "bg-amber-500/10 text-amber-300")
          }
        >
          {isConnecting ? (
            <Loader2 size={15} className="animate-spin" />
          ) : state === "error" ? (
            <AlertTriangle size={15} />
          ) : (
            <WifiOff size={15} />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-minimax-fg">{copy.title}</div>
          <div className="truncate text-minimax-muted">
            {copy.detail} Next retry in {retrySeconds}s.
          </div>
        </div>
        <button
          type="button"
          data-testid="connection-retry"
          onClick={() => void onRetry()}
          className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-minimax-border px-2 font-medium text-minimax-fg transition-colors duration-200 hover:bg-minimax-border"
        >
          <RefreshCw size={12} />
          Retry
        </button>
      </div>
    </div>
  );
}
