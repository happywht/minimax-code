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
  /** Provider ID — populated by the dynamic model list. */
  provider_id?: string;
  /** Protocol used by the parent provider ("anthropic" | "openai"). */
  protocol?: string;
}

/** A model entry nested inside a provider. */
export interface ProviderModel {
  id: string;
  name: string;
  context_window: number;
  supports_tools: boolean;
  is_default?: boolean;
}

/** An LLM provider record — matches the `providers` table row. */
export interface ProviderInfo {
  id: string;
  name: string;
  protocol: "anthropic" | "openai";
  base_url: string;
  /** Whether an API key is stored in keyring for this provider. */
  api_key_configured: boolean;
  models: ProviderModel[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
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
  /**
   * Optional per-turn metadata — populated on assistant messages
   * by the chat store. Carries the v0.3.0 ``thinking_count`` wire
   * shape (``{thinking_count, tokens_in, tokens_out}``) the
   * :class:`MessageItem` summary row reads from. We keep a copy
   * here so the summary renders correctly even when the message
   * is re-rendered before the next ``agent.message_chunk`` event.
   */
  metadata?: MessageMetadata;
}

/** Per-turn metadata snapshot for the v0.3.0 thinking_count channel. */
export interface MessageMetadata {
  thinking_count: number;
  tokens_in: number;
  tokens_out: number;
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
  system_prompt?: string;
  tool_allowlist?: string[];
  model?: string;
}

/** Params for `agent.spawn_subagent` (extended in v0.3.0 §2). */
export interface SpawnSubagentParams {
  agent_id: string;
  prompt: string;
  parent_session_id?: string;
  context_message_id?: string;
  display_name?: string;
}

/** A permission rule. */
export interface PermissionRule {
  id: string;
  tool: string;
  pattern: string;
  decision: "allow" | "deny" | "ask";
  created_at: number;
}

/** An audit log entry — matches `audit_log` table row. */
export interface AuditEntry {
  id: string;
  session_id: string | null;
  tool_name: string;
  tool_args: string | null;
  permission: string | null;
  result_status: "success" | "fail" | "timeout" | "denied";
  exit_code: number | null;
  duration_ms: number | null;
  error: string | null;
  created_at: string;
}

/** Result shape for `audit.list`. */
export interface ListAuditResult {
  entries: AuditEntry[];
  total: number;
}

/** Aggregate audit stats — result of `audit.stats`. */
export interface AuditStats {
  total: number;
  by_tool: Record<string, number>;
  by_status: Record<string, number>;
}

/** A webhook config entry — matches `webhooks` table row. */
export interface WebhookConfig {
  id: string;
  name: string;
  source: "github" | "gitee" | "custom";
  url_path: string;
  secret: string | null;
  enabled: boolean;
  action_type: "code-review" | "send-message";
  action_config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** Result shape for `webhook.list`. */
export interface ListWebhooksResult {
  entries: WebhookConfig[];
  total: number;
}

/* ─────────────────────── Event payload shapes ─────────────────────── */

export interface MessageChunkData {
  session_id: string;
  message_id: string;
  delta: string;
  done: boolean;
  /**
   * Optional per-turn metadata — populated on the trailing
   * ``done=True`` chunk (and possibly the first text chunk) by
   * the v0.3.0 ``thinking_count`` wire format. Earlier chunks in
   * the same turn omit the field; the store keeps the latest
   * non-null value per message so the UI sees a stable snapshot
   * even before the stream ends.
   */
  metadata?: MessageMetadata;
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

/** Return shape of `session.update`. */
export interface UpdateSessionResult {
  ok: boolean;
  session: Session;
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

export interface ListProvidersResult {
  providers: ProviderInfo[];
}

export interface CreateProviderResult {
  provider: ProviderInfo;
}

export interface UpdateProviderResult {
  provider: ProviderInfo;
}

export interface DeleteProviderResult {
  ok: true;
  deleted: string;
}

export interface SetProviderApiKeyResult {
  ok: true;
  provider_id: string;
  api_key_configured: boolean;
}

export interface SetRuleResult {
  rule: PermissionRule;
}

export interface SpawnSubagentResult {
  agent_run_id: string;
  agent_id: string;
}

/* ─────────────────────── Git integration (§3-4 of v0.3.0 design) ─────────────────────── */

/** Return shape of `git.status`. */
export interface GitStatusResult {
  branch: string;
  clean: boolean;
  ahead: number;
  behind: number;
  modified: string[];
  untracked: string[];
  staged: string[];
}

/** Return shape of `git.diff`. */
export interface GitDiffResult {
  scope: string;
  ref?: string;
  diff: string;
}

/** A single `git.log` entry. */
export interface GitLogEntry {
  sha: string;
  author: string;
  message: string;
  files_changed: string[];
}

/** Return shape of `git.log`. */
export interface GitLogResult {
  entries: GitLogEntry[];
}

/* ─────────────────────── Sub-agent progress (§2 of v0.3.0 design) ─────────────────────── */

/** Lifecycle status of a single sub-agent run. */
export type SubAgentStatus =
  | "started"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "completed"
  | "failed";

/** Wire shape of one ``agent.subagent_progress`` event. */
export interface SubAgentProgress {
  run_id: string;
  agent_id: string;
  parent_session_id?: string;
  context_message_id?: string;
  status: SubAgentStatus;
  progress: number; // 0..1
  summary: string;
  /** Final text once ``status === "completed"``. */
  text?: string;
  /** Error message once ``status === "failed"``. */
  error?: string;
  /** Wall-clock timestamp set by the receiver (ms since epoch). */
  received_at: number;
}

/** Frontend-tracked row for a single sub-agent run (UI state). */
export interface SubAgentRun {
  run_id: string;
  agent_id: string;
  agent_name: string;
  display_name?: string;
  parent_session_id?: string;
  context_message_id?: string;
  prompt: string;
  status: SubAgentStatus;
  progress: number;
  summary: string;
  text?: string;
  error?: string;
  started_at: number;
  updated_at: number;
  /** Set once ``status === "completed"`` | ``"failed"``. */
  finished_at?: number;
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
  SubAgentProgress: "agent.subagent_progress",
} as const;

export type StreamEventName = (typeof StreamEvent)[keyof typeof StreamEvent];

/** Shape for the optional sidecar status event from Rust. */
export interface SidecarEvent {
  status: "started" | "stopped" | "error";
  error?: string;
}
