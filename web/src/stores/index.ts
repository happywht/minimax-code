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
