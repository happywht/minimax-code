import { FlaskConical } from "lucide-react";
import { strings } from "../../ui/strings";

export interface MockModeBannerProps {
  active: boolean;
}

/**
 * Accent-toned banner shown while the UI is running against the
 * in-process mock backend — either forced via ``VITE_AGENT_MODE=mock``
 * or degraded after the agent probe failed. Without it the mock path
 * looks identical to a live session, which misleads anyone demoing or
 * debugging into thinking they are talking to a real agent.
 * Not dismissible: mock mode is a backend state, not a notice.
 */
export function MockModeBanner({ active }: MockModeBannerProps): JSX.Element | null {
  if (!active) return null;

  return (
    <aside
      data-testid="mock-banner"
      role="status"
      aria-live="polite"
      className="shrink-0 border-b border-violet-500/25 bg-violet-500/10 px-3 py-2 sm:px-4"
    >
      <div className="mx-auto flex w-full max-w-[780px] items-center gap-2.5">
        <FlaskConical size={14} aria-hidden="true" className="shrink-0 text-violet-300" />
        <div className="min-w-0 flex-1 text-[11px] leading-4">
          <span className="font-medium text-violet-200">{strings.layout.mock.title}</span>
          <span className="ml-1.5 text-minimax-muted">
            {strings.layout.mock.detail}
          </span>
        </div>
      </div>
    </aside>
  );
}
