import { AlertTriangle, RefreshCw, WifiOff, X } from "lucide-react";
import { Button, IconButton } from "../../ui";
import { Spinner } from "../../ui/Spinner";
import { strings } from "../../ui/strings";

export type ConnectionBannerState = "connecting" | "disconnected" | "error";

export interface ConnectionBannerProps {
  state: ConnectionBannerState;
  retryDelayMs: number;
  onRetry: () => void | Promise<void>;
  onDismiss?: () => void;
}

const COPY: Record<ConnectionBannerState, { title: string; detail: string }> = {
  connecting: {
    title: strings.layout.connection.connectingTitle,
    detail: strings.layout.connection.connectingDetail,
  },
  disconnected: {
    title: strings.layout.connection.disconnectedTitle,
    detail: strings.layout.connection.disconnectedDetail,
  },
  error: {
    title: strings.layout.connection.errorTitle,
    detail: strings.layout.connection.errorDetail,
  },
};

export function ConnectionBanner({
  state,
  retryDelayMs,
  onRetry,
  onDismiss,
}: ConnectionBannerProps): JSX.Element {
  const copy = COPY[state];
  const retrySeconds = Math.max(1, Math.ceil(retryDelayMs / 1000));
  const isConnecting = state === "connecting";

  return (
    <div
      data-testid="connection-banner"
      className="pointer-events-none fixed inset-x-3 top-3 z-40 mx-auto max-w-2xl animate-rise-in rounded-lg border border-line bg-surface-1/95 px-3 py-2 text-xs shadow-pop backdrop-blur"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <div
          className={
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-md " +
            (state === "error"
              ? "bg-status-error/10 text-status-error"
              : "bg-status-warning/10 text-status-warning")
          }
        >
          {isConnecting ? (
            <Spinner size={15} />
          ) : state === "error" ? (
            <AlertTriangle size={15} />
          ) : (
            <WifiOff size={15} />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-ink-0">{copy.title}</div>
          <div className="truncate text-ink-1">
            {copy.detail} {strings.layout.connection.nextRetry(retrySeconds)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            data-testid="connection-retry"
            icon={<RefreshCw size={12} />}
            onClick={() => void onRetry()}
            className="pointer-events-auto"
          >
            {strings.layout.connection.retry}
          </Button>
          {onDismiss && (
            <IconButton
              aria-label={strings.layout.connection.dismiss}
              title={strings.common.close}
              size="sm"
              data-testid="connection-dismiss"
              onClick={onDismiss}
              className="pointer-events-auto text-ink-2 hover:text-ink-0"
            >
              <X size={14} />
            </IconButton>
          )}
        </div>
      </div>
    </div>
  );
}
