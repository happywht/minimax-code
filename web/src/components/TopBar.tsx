/**
 * TopBar — the 40px header strip across the top of the app.
 *
 * Three slots, left to right:
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │  [≡] [WorkspaceSwitcher] [GitStatusBar] … [🌙][⚙️]  │
 *   └──────────────────────────────────────────────────────┘
 *
 * The hamburger button (≡) is only visible on mobile (< md) and
 * toggles the sidebar overlay. Desktop users always see the sidebar.
 *
 * Extracted out of ``App.tsx`` in v0.3.0 so the
 * ``GitStatusBar`` can live next to the workspace switcher
 * without dragging in the rest of the shell.
 */
import type { ReactNode } from "react";
import { Menu, Settings as SettingsIcon } from "lucide-react";
import { GitStatusBar } from "./GitStatusBar";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { ThemeToggle } from "./ThemeToggle";

export interface TopBarProps {
  testId?: string;
  /** Render-prop for the right-hand action cluster. */
  rightSlot?: ReactNode;
  onOpenSettings?: () => void;
  /** Toggle the mobile sidebar overlay. Ignored on md+ screens. */
  onToggleSidebar?: () => void;
}

export function TopBar({
  testId = "app-topbar",
  rightSlot,
  onOpenSettings,
  onToggleSidebar,
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
        {onToggleSidebar && (
          <button
            type="button"
            data-testid="app-topbar-hamburger"
            onClick={onToggleSidebar}
            aria-label="Toggle sidebar"
            className="flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg md:hidden"
          >
            <Menu size={16} />
          </button>
        )}
        <WorkspaceSwitcher />
        <GitStatusBar />
      </div>
      <div
        data-testid="app-topbar-right"
        className="flex items-center gap-2"
      >
        {rightSlot}
        <ThemeToggle />
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
