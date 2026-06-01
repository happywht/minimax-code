/**
 * Component index — re-exports for ergonomic imports from
 * `@/components`. Grouped by feature area; the test suite imports
 * from here too.
 */

export { Sidebar } from "./Sidebar";
export { NavItem } from "./NavItem";
export { ChatPanel } from "./ChatPanel";
export { MessageList } from "./MessageList";
export { MessageItem } from "./MessageItem";
export { MessageInput } from "./MessageInput";
export { ProgressPanel } from "./ProgressPanel";
export { ModelSelector } from "./ModelSelector";
export { PermissionToggle } from "./PermissionToggle";
export { UserBadge } from "./UserBadge";
export { ErrorBoundary, ToastViewport, toast, toastBus } from "./ErrorBoundary";
export type { ToastItem, ToastKind } from "./ErrorBoundary";
