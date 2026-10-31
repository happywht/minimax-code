/**
 * TopBar — the 40px header strip across the top of the app.
 */
import type { ReactNode } from "react";
import { Command, Eye, Menu, MoreHorizontal, PanelRight, Settings as SettingsIcon } from "lucide-react";
import { GitStatusBar } from "./GitStatusBar";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { ThemeToggle } from "./ThemeToggle";
import { NotificationBell } from "./NotificationBell";
import { IconButton, Button } from "../../ui";
import { strings } from "../../ui/strings";

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
  /** Toggle the command palette. */
  onToggleCommandPalette?: () => void;
  /** Open the inspector drawer. Ignored on lg+ screens (inline panel). */
  onToggleInspector?: () => void;
}

export function TopBar({
  testId = "app-topbar",
  rightSlot,
  onOpenSettings,
  onToggleSidebar,
  onTogglePreview,
  previewActive = false,
  onToggleCommandPalette,
  onToggleInspector,
}: TopBarProps): JSX.Element {
  return (
    <header
      data-testid={testId}
      className="flex h-14 w-full shrink-0 items-center justify-between gap-1 border-b border-line/50 bg-surface-0 px-3 sm:gap-2 md:px-5"
    >
      <div
        data-testid="app-topbar-left"
        className="flex min-w-0 flex-1 items-center gap-2"
      >
        {onToggleSidebar && (
          <IconButton
            size="md"
            aria-label={strings.layout.topbar.toggleSidebar}
            title={strings.layout.topbar.toggleSidebar}
            onClick={onToggleSidebar}
            data-testid="app-topbar-hamburger"
            className="md:hidden"
          >
            <Menu size={16} />
          </IconButton>
        )}
        <WorkspaceSwitcher />
      </div>
      <div
        data-testid="app-topbar-right"
        className="flex shrink-0 items-center gap-0.5 sm:gap-1"
      >
        {rightSlot}
        {onToggleInspector && (
          <IconButton
            aria-label={strings.layout.topbar.toggleInspector}
            title={strings.layout.topbar.toggleInspector}
            onClick={onToggleInspector}
            data-testid="app-topbar-inspector"
            // md–lg only: from lg up the inspector lives inline instead.
            className="md:inline-flex lg:hidden"
          >
            <PanelRight size={14} />
          </IconButton>
        )}
        <IconButton
          aria-label={strings.layout.topbar.openCommandPalette}
          title={strings.layout.topbar.commandPaletteHint}
          onClick={onToggleCommandPalette}
          data-testid="app-topbar-command-palette"
        >
          <Command size={14} />
        </IconButton>
        <details className="relative">
          <summary aria-label="更多操作" title="更多操作" className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-lg text-ink-1 hover:bg-surface-3 hover:text-ink-0 [&::-webkit-details-marker]:hidden">
            <MoreHorizontal size={17} />
          </summary>
          <div className="absolute right-0 top-full z-50 mt-2 flex w-56 flex-col items-stretch gap-2 rounded-xl border border-line bg-surface-1 p-3 shadow-pop">
            <div className="text-xs font-medium text-ink-2">仓库状态</div>
            <GitStatusBar />
            <div className="flex items-center gap-1 border-t border-line pt-2">
              {onTogglePreview && (
                <Button size="sm" variant={previewActive ? "subtle" : "ghost"} icon={<Eye size={14} />} onClick={onTogglePreview} data-testid="app-topbar-preview">
                  {strings.layout.topbar.preview}
                </Button>
              )}
              <NotificationBell />
              <ThemeToggle />
              <IconButton aria-label={strings.layout.topbar.settings} title={strings.layout.topbar.settings} onClick={onOpenSettings} data-testid="app-topbar-settings">
                <SettingsIcon size={14} />
              </IconButton>
            </div>
          </div>
        </details>
      </div>
    </header>
  );
}
