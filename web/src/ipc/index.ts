/**
 * Index of the IPC module — re-exports the singleton and types so the
 * rest of the app imports from `@/ipc` instead of reaching into
 * individual files.
 */
export {
  ipc,
  IPCClient,
  IPCError,
  /**
   * @deprecated Use `isAgentReachable` for runtime detection.
   * Retained for backward compatibility with `App.tsx`; always
   * returns `false` since we no longer run inside a Tauri webview.
   */
  isTauri,
  isAgentReachable,
  fetchHealth,
  typedIPC,
  bindTypedIPC,
} from "./client";
export type { IPCClientOptions, TypedIPC, StreamEventPayload, AgentHealth } from "./client";
export {
  ErrorCode,
  StreamEvent,
} from "../types/ipc";
export type {
  JsonRpcRequest,
  JsonRpcResponse,
  JsonRpcNotification,
  JsonRpcEvent,
  JsonRpcError,
  MessageChunkData,
  SendMessageResult,
  StatusResult,
  PingResult,
  SidecarEvent,
  StreamEventName,
  AgentStatusData,
  ToolCallData,
  ToolResultData,
  PermissionRequestData,
  PermissionResolvedData,
  TaskProgressData,
  Message,
  MessageRole,
  Session,
  ModelInfo,
  SkillInfo,
  ScheduledJob,
  AgentInfo,
  PermissionRule,
} from "../types/ipc";
