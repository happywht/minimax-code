import { DatabaseZap } from "lucide-react";

export interface StorageBannerProps {
  degraded: boolean;
}

/**
 * Persistent amber banner shown when the agent reports its storage
 * layer as degraded (`/health` → `db: false`). In this state new
 * sessions and messages are not persisted; restarting the agent
 * usually recovers it. Not dismissible — the condition outlives any
 * action the user could take from the banner itself.
 */
export function StorageBanner({ degraded }: StorageBannerProps): JSX.Element | null {
  if (!degraded) return null;

  return (
    <aside
      data-testid="storage-banner"
      role="status"
      aria-live="polite"
      className="shrink-0 border-b border-amber-500/20 bg-amber-500/5 px-3 py-2 sm:px-4"
    >
      <div className="mx-auto flex w-full max-w-[780px] items-center gap-2.5">
        <DatabaseZap size={14} aria-hidden="true" className="shrink-0 text-amber-300" />
        <div className="min-w-0 flex-1 text-[11px] leading-4">
          <span className="font-medium text-amber-200">Local Storage Unavailable</span>
          <span className="ml-1.5 text-minimax-muted">
            New sessions and messages won&apos;t be saved. Restarting the agent usually
            recovers this.
          </span>
        </div>
      </div>
    </aside>
  );
}
