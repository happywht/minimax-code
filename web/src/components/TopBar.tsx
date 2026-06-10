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
import { Eye, Menu, Settings as SettingsIcon } from "lucide-react";
import { GitStatusBar } from "./GitStatusBar";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { ThemeToggle } from "./ThemeToggle";
import { NotificationBell } from "./NotificationBell";

export interface TopBarProps {
  testId?: string;
  /** Render-prop for the right-hand action cluster. */
  rightSlot?: ReactNode;
  onOpenSettings?: () => void;
  /** Toggle the mobile sidebar overlay. Ignored on md+ screens. */
  onToggleSidebar?: () => void;
  /** Toggle the live-preview panel. */
  onTogglePreview?: () => void;
  /** Whether the preview panel is currently active. */
  previewActive?: boolean;
}

export function TopBar({
  testId = "app-topbar",
  rightSlot,
  onOpenSettings,
  onToggleSidebar,
  onTogglePreview,
  previewActive = false,
}: TopBarProps): JSX.Element {
  return (
    <header
      data-testid={testId}
      className="flex h-10 w-full shrink-0 items-center justify-between gap-2 overflow-hidden border-b border-minimax-border bg-minimax-panel px-2 md:px-4"
    >
      <div
        data-testid="app-topbar-left"
        className="flex min-w-0 flex-1 items-center gap-2"
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
        className="flex shrink-0 items-center gap-1 sm:gap-2"
      >
        {rightSlot}
        {onTogglePreview && (
          <button
            type="button"
            data-testid="app-topbar-preview"
            onClick={onTogglePreview}
            className={`flex h-7 items-center gap-1.5 rounded-md border px-1.5 text-xs sm:px-2 ${
              previewActive
                ? "border-minimax-accent text-minimax-accent"
                : "border-transparent text-minimax-fg/80 hover:border-minimax-border hover:text-minimax-fg"
            }`}
            title="Toggle live preview"
          >
            <Eye size={12} />
            <span className="hidden sm:inline">Preview</span>
          </button>
        )}
        <NotificationBell />
        <ThemeToggle />
        <button
          type="button"
          data-testid="app-topbar-settings"
          onClick={onOpenSettings}
          className="flex h-7 items-center gap-1.5 rounded-md border border-transparent px-1.5 text-xs text-minimax-fg/80 hover:border-minimax-border hover:text-minimax-fg sm:px-2"
          aria-label="Settings"
        >
          <SettingsIcon size={12} className="text-minimax-muted" />
          <span className="hidden sm:inline">Settings</span>
        </button>
      </div>
    </header>
  );
}
