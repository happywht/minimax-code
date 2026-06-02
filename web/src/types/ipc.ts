/**
 * JSON-RPC 2.0 envelope types + shared protocol types — must match
 * `agent/minimax_code/ipc/protocol.py`.
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

/* ───────────────────────── Domain types ───────────────────────── */

/** A model entry returned by `model.list`. */
export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  context_window: number;
  supports_tools: boolean;
  is_default?: boolean;
}

/** A session record returned by `session.list` / `session.create`. */
export interface Session {
  id: string;
  title: string;
  archived: boolean;
  created_at: number;
  updated_at: number;
  model_id: string | null;
  message_count?: number;
}

/** A persisted chat message (also used for in-flight streaming). */
export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  /** True while a stream is still in progress. */
  streaming: boolean;
  created_at: number;
  /** Optional tool call metadata for assistant messages. */
  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  /** Optional parent linkage (e.g. tool_result of a tool_call). */
  parent_id?: string;
}

/** A scheduled job record. */
export interface ScheduledJob {
  id: string;
  name: string;
  cron: string;
  prompt: string;
  enabled: boolean;
  last_run_at: number | null;
  next_run_at: number | null;
}

/** A skill record. */
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
}

/** A sub-agent record. */
export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
}

/** A permission rule. */
export interface PermissionRule {
  id: string;
  tool: string;
  pattern: string;
  decision: "allow" | "deny" | "ask";
  created_at: number;
}

/* ─────────────────────── Event payload shapes ─────────────────────── */

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

export interface ToolCallData {
  session_id: string;
  tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
  message_id: string;
}

export interface ToolResultData {
  session_id: string;
  tool_call_id: string;
  result: unknown;
  error?: string;
  message_id: string;
}

export interface PermissionRequestData {
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface PermissionResolvedData {
  request_id: string;
  decision: "allow" | "deny";
}

export interface TaskProgressData {
  task_id: string;
  progress: number; // 0..1
  message?: string;
  status: "running" | "done" | "error" | "cancelled";
}

/* ─────────────────────── Method-result shapes ─────────────────────── */

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

export interface ListSessionsResult {
  sessions: Session[];
}

export interface CreateSessionResult {
  session_id: string;
}

export interface ListMessagesResult {
  messages: Message[];
}

export interface ListModelsResult {
  models: ModelInfo[];
  current: string | null;
}

export interface ListSkillsResult {
  skills: SkillInfo[];
}

export interface ListAgentsResult {
  agents: AgentInfo[];
}

export interface ListJobsResult {
  jobs: ScheduledJob[];
}

/** Snapshot of the API key state — return shape of `secrets.status`. */
export interface SecretStatus {
  configured: boolean;
  source: "keyring" | "env" | "none";
}

export interface ListRulesResult {
  rules: PermissionRule[];
}

export interface SetModelResult {
  current: string;
}

export interface SetRuleResult {
  rule: PermissionRule;
}

export interface SpawnSubagentResult {
  agent_run_id: string;
  agent_id: string;
}

/** All known event names — used to keep listeners strongly typed. */
export const StreamEvent = {
  MessageChunk: "agent.message_chunk",
  AgentStatus: "agent.status",
  ToolCall: "agent.tool_call",
  ToolResult: "agent.tool_result",
  PermissionRequest: "permission.request",
  PermissionResolved: "permission.resolved",
  TaskProgress: "task.progress",
} as const;

export type StreamEventName = (typeof StreamEvent)[keyof typeof StreamEvent];

/** Shape for the optional sidecar status event from Rust. */
export interface SidecarEvent {
  status: "started" | "stopped" | "error";
  error?: string;
}
