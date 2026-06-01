/**
 * Index of the IPC module — re-exports the singleton and types so the
 * rest of the app imports from `@/ipc` instead of reaching into
 * individual files.
 */
export {
  ipc,
  IPCClient,
  IPCError,
  isTauri,
  typedIPC,
  bindTypedIPC,
  ErrorCode,
} from "./client";
export type { TypedIPC, StreamEventPayload } from "./client";
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
  StreamEvent,
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
