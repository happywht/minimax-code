/**
 * Index of the IPC module — re-exports the singleton and types so the
 * rest of the app imports from `@/ipc` instead of reaching into
 * individual files.
 */
export { ipc, IPCClient, ErrorCode } from "./client";
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
} from "../types/ipc";
