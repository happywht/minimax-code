/**
 * TopBar — the 40px header strip across the top of the app.
 *
 * Three slots, left to right:
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │  [WorkspaceSwitcher] [GitStatusBar]   …   [Settings] │
 *   └──────────────────────────────────────────────────────┘
 *
 * The right side is reserved for the settings shortcut;
 * future widgets (e.g. a "what's new" badge) hook into the
 * flex-1 spacer between the two clusters so they stay
 * right-aligned with the icon button.
 *
 * Extracted out of ``App.tsx`` in v0.3.0 so the
 * ``GitStatusBar`` can live next to the workspace switcher
 * without dragging in the rest of the shell. The shell
 * composition (Sidebar / ChatPanel / RightPanel) is
 * unchanged.
 */
import type { ReactNode } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import { GitStatusBar } from "./GitStatusBar";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

export interface TopBarProps {
  testId?: string;
  /** Render-prop for the right-hand action cluster. */
  rightSlot?: ReactNode;
  onOpenSettings?: () => void;
}

export function TopBar({
  testId = "app-topbar",
  rightSlot,
  onOpenSettings,
}: TopBarProps): JSX.Element {
  return (
    <header
      data-testid={testId}
      className="flex h-10 shrink-0 items-center justify-between border-b border-minimax-border bg-minimax-panel px-4"
    >
      <div
        data-testid="app-topbar-left"
        className="flex items-center gap-2"
      >
        <WorkspaceSwitcher />
        <GitStatusBar />
      </div>
      <div
        data-testid="app-topbar-right"
        className="flex items-center gap-2"
      >
        {rightSlot}
        <button
          type="button"
          data-testid="app-topbar-settings"
          onClick={onOpenSettings}
          className="flex items-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-xs text-minimax-fg/80 hover:border-minimax-border hover:text-minimax-fg"
        >
          <SettingsIcon size={12} className="text-minimax-muted" />
          <span>Settings</span>
        </button>
      </div>
    </header>
  );
}
