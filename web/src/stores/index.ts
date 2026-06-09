/**
 * Zustand stores. Each store owns a slice of UI state and re-uses the
 * shared IPC client for backend round-trips.
 *
 *   - chat:     streaming message log + send() round-trip
 *   - session:  session list, current session id
 *   - model:    available models + current selection
 *   - permission: "always allow" toggle + rules
 *   - task:     long-running task progress entries
 */
export { useChat, type Message, type ChatStatus } from "./chat";
export {
  useSessionStore,
  type SessionMeta,
  type SessionFilter,
} from "./sessionStore";
export {
  useModelStore,
  type ModelEntry,
} from "./modelStore";
export {
  usePermissionStore,
  type PermissionRuleEntry,
  type PendingPermission,
  type Decision as PermissionDecision,
  _resetPermissionStoreListeners,
} from "./permissionStore";
export {
  useTaskStore,
  type TaskProgressEntry,
  type TaskStatus,
} from "./taskStore";
export {
  useRunTimelineStore,
  type RunTimelineEntry,
} from "./runStore";
export {
  useScheduleStore,
  type ScheduledJobEntry,
} from "./scheduleStore";
export {
  useSecretStore,
  type SecretSource,
} from "./secretStore";
export {
  useSkillStore,
  type SkillEntry,
} from "./skillStore";
export {
  useSubAgentStore,
  runsForSession,
  type SubAgentState,
} from "./subAgent";
export { useGitStore, type GitState } from "./git";
export { useThemeStore, type Theme } from "./themeStore";
export { useAgentStore } from "./agentStore";
export { useMobileStore, type PairedDevice } from "./mobileStore";
export { useCodeReviewStore, type ReviewComment, type ReviewStats } from "./codeReviewStore";
export { useProviderStore } from "./providerStore";
export type { ProviderInfo } from "../types/ipc";
export { useAuditStore, type AuditState } from "./auditStore";
export { useWebhookStore, type WebhookState } from "./webhookStore";
export { usePreviewStore, type PreviewState } from "./previewStore";
export {
  useNotificationStore,
  initNotificationStore,
  type NotificationEntry,
  type ListNotificationsResult,
} from "./notificationStore";
export {
  useWorkflowStore,
  type WorkflowState,
} from "./workflowStore";
export {
  useTeamStore,
  type TeamState,
} from "./teamStore";
export {
  useTeamRunStore,
  initTeamRunListener,
  type TeamRunState,
  type TeamRunEntry,
} from "./teamRunStore";
