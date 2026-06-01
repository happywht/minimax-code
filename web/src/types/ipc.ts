/**
 * JSON-RPC 2.0 envelope types — must match `agent/minimax_code/ipc/protocol.py`.
 *
 * The Rust sidecar re-emits the raw JSON; the frontend is responsible
 * for type-narrowing with these interfaces.
 */

export type JsonRpcId = string | number;

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: JsonRpcId;
  method: string;
  params?: unknown;
}

export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

export interface JsonRpcResponse<T = unknown> {
  jsonrpc: "2.0";
  id: JsonRpcId;
  result?: T;
  error?: JsonRpcError;
}

export interface JsonRpcError {
  code: number;
  message: string;
  data?: unknown;
}

export interface JsonRpcEvent<T = unknown> {
  jsonrpc: "2.0";
  event: string;
  data?: T;
}

/** Standard error codes. */
export const ErrorCode = {
  ParseError: -32700,
  InvalidRequest: -32600,
  MethodNotFound: -32601,
  InvalidParams: -32602,
  InternalError: -32603,
  ToolExecutionError: -32001,
  PermissionDenied: -32002,
  LLMError: -32003,
  StorageError: -32004,
  NotImplemented: -32005,
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

/** Event payload shapes — see docs/ipc-contract.md. */
export interface MessageChunkData {
  session_id: string;
  message_id: string;
  delta: string;
  done: boolean;
}

export interface AgentStatusData {
  session_id: string;
  status: "thinking" | "tool_call" | "tool_result" | "idle" | "error";
  detail?: string;
}

/** Method-result shapes. */
export interface PingResult {
  pong: number;
  uptime_s: number;
  server: string;
}

export interface StatusResult extends PingResult {
  python: string;
  version: string;
  active_sessions: number;
}

export interface SendMessageResult {
  session_id: string;
  message_id: string;
  text: string;
}
