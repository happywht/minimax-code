/**
 * Component index — re-exports for ergonomic imports from
 * `@/components`. Grouped by feature area; the test suite imports
 * from here too.
 */

export { Sidebar } from "./layout/Sidebar";
export { NavItem } from "./layout/NavItem";
export { ChatPanel } from "./chat/ChatPanel";
export { ProviderReadinessBanner } from "./chat/ProviderReadinessBanner";
export type { ProviderReadinessBannerProps } from "./chat/ProviderReadinessBanner";
export { MessageList } from "./chat/MessageList";
export { MessageInput } from "./chat/MessageInput";
export { ProgressPanel } from "./right-panel/ProgressPanel";
export { RightPanel } from "./RightPanel";
export { ModelSelector } from "./chat/ModelSelector";
export { ReasoningEffortBadge } from "./chat/ReasoningEffortBadge";
export type { ReasoningEffortBadgeProps } from "./chat/ReasoningEffortBadge";
export { PermissionRequestModal } from "./modals/PermissionRequestModal";
export { ConfirmationDialog, confirmationBus, requestConfirmation } from "./modals/ConfirmationDialog";
export type { ConfirmationOptions } from "./modals/ConfirmationDialog";
export { PermissionPatchPreview } from "./modals/PermissionPatchPreview";
export type { PermissionPatchPreviewProps } from "./modals/PermissionPatchPreview";
export { UserBadge } from "./layout/UserBadge";
export { ErrorBoundary, ToastViewport, toast, toastBus } from "./layout/ErrorBoundary";
export type { ToastItem, ToastKind } from "./layout/ErrorBoundary";
export { SettingsPage } from "./settings/SettingsPage";
export { SkillsPanel } from "./panels/SkillsPanel";
export { WorkspaceSwitcher } from "./layout/WorkspaceSwitcher";
export { SubAgentPanel } from "./right-panel/SubAgentPanel";
export { SubAgentResultCard } from "./right-panel/SubAgentResultCard";
export { GitStatusBar } from "./layout/GitStatusBar";
export { GitViewerModal } from "./modals/GitViewerModal";
export type { GitViewerModalProps } from "./modals/GitViewerModal";
export { PatchPreviewPanel } from "./panels/PatchPreviewPanel";
export type { PatchPreviewPanelProps } from "./panels/PatchPreviewPanel";
export { TerminalPanel } from "./right-panel/TerminalPanel";
export type { TerminalPanelProps } from "./right-panel/TerminalPanel";
export { RunnerPanel } from "./right-panel/RunnerPanel";
export type { RunnerPanelProps } from "./right-panel/RunnerPanel";
export { ThemeToggle } from "./layout/ThemeToggle";
export type { ThemeToggleProps } from "./layout/ThemeToggle";
export { TopBar } from "./layout/TopBar";
export { CommandPalette } from "./layout/CommandPalette";
export { MobilePairingModal } from "./modals/MobilePairingModal";
export type { MobilePairingModalProps } from "./modals/MobilePairingModal";
export { CodeReviewPanel } from "./panels/CodeReviewPanel";
export type { CodeReviewPanelProps } from "./panels/CodeReviewPanel";
export { ImagePreview } from "./panels/ImagePreview";
export type { ImagePreviewProps } from "./panels/ImagePreview";
export { PreviewPanel } from "./panels/PreviewPanel";
export type { PreviewPanelProps } from "./panels/PreviewPanel";
export { NotificationBell } from "./layout/NotificationBell";
export { NotificationCenter } from "./layout/NotificationCenter";
export { ShortcutsOverlay } from "./layout/ShortcutsOverlay";
export type { ShortcutsOverlayProps } from "./layout/ShortcutsOverlay";
export { ConnectionBanner } from "./layout/ConnectionBanner";
export type { ConnectionBannerProps, ConnectionBannerState } from "./layout/ConnectionBanner";
export { CrashRecoveryPrompt } from "./modals/CrashRecoveryPrompt";
