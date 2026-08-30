/**
 * TopBar — the 40px header strip across the top of the app.
 */
import type { ReactNode } from "react";
import { Command, Eye, Menu, PanelRight, Settings as SettingsIcon } from "lucide-react";
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
      className="flex h-10 w-full shrink-0 items-center justify-between gap-2 border-b border-line bg-surface-1 px-2 md:px-3"
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
        <GitStatusBar />
      </div>
      <div
        data-testid="app-topbar-right"
        className="flex shrink-0 items-center gap-1"
      >
        {rightSlot}
        {onToggleInspector && (
          <IconButton
            aria-label={strings.layout.topbar.toggleInspector}
            title={strings.layout.topbar.toggleInspector}
            onClick={onToggleInspector}
            data-testid="app-topbar-inspector"
            // md–lg only: from lg up the inspector lives inline instead.
            // (the leading `hidden` matters: without it the button is
            // visible on phones, where the drawer is lg-guarded anyway)
            className="hidden md:inline-flex lg:hidden"
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
        {onTogglePreview && (
          <Button
            size="sm"
            variant={previewActive ? "subtle" : "ghost"}
            icon={<Eye size={14} />}
            onClick={onTogglePreview}
            data-testid="app-topbar-preview"
            className="hidden sm:inline-flex"
          >
            {strings.layout.topbar.preview}
          </Button>
        )}
        <NotificationBell />
        <ThemeToggle />
        <Button
          size="sm"
          variant="ghost"
          icon={<SettingsIcon size={14} />}
          onClick={onOpenSettings}
          data-testid="app-topbar-settings"
        >
          <span className="hidden sm:inline">{strings.layout.topbar.settings}</span>
        </Button>
      </div>
    </header>
  );
}
