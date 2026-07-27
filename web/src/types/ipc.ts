/**
 * JSON-RPC 2.0 envelope types + shared protocol types — must match
 * `agent/minimax_code/ipc/protocol.py`.
 *
 * The Rust sidecar re-emits the raw JSON; the frontend is responsible
 * for type-narrowing with these interfaces.
 */

export type JsonRpcId = string | number | null;

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

/**
 * A selectable reasoning-effort menu entry (R58 enrich consumer surface).
 *
 * Mirrors the backend ``ReasoningEffortOption`` pydantic model emitted by
 * ``reasoning_efforts_meta_value`` — ``value`` is the canonical wire token
 * ("none" | "minimal" | "low" | "medium" | "high" | "xhigh"); ``id`` / ``label``
 * are presentation; ``description`` is optional long-form; ``default`` marks the
 * model's default tier. A model that declares no reasoning-effort meta omits
 * the parent ``reasoning_effort_options`` array entirely (zero regression —
 * the three enrich fields are all optional and only attached when the model's
 * catalog meta declares them).
 */
export interface ReasoningEffortOption {
  value: string;
  id: string;
  label: string;
  description: string | null;
  default: boolean;
}

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
  /** Whether the model supports reasoning-effort control (R58 enrich). */
  supports_reasoning_effort?: boolean;
  /** Default reasoning-effort wire token, e.g. "high" (R58 enrich). */
  reasoning_effort_default?: string;
  /** Selectable reasoning-effort menu (R58 enrich). */
  reasoning_effort_options?: ReasoningEffortOption[];
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
  project_id?: string;
  model_id: string | null;
  message_count?: number;
  workspace_mode?: "local" | "worktree";
  workspace_path?: string | null;
  worktree_branch?: string | null;
  base_branch?: string | null;
}

/** A persisted chat message (also used for in-flight streaming). */
export type MessageRole = "user" | "assistant" | "system" | "tool";
export type MessageStatus =
  | "queued"
  | "sending"
  | "streaming"
  | "completed"
  | "failed"
  | "cancelling"
  | "cancelled";

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  /** True while a stream is still in progress. */
  streaming: boolean;
  /** Optional frontend lifecycle state used for richer rendering. */
  status?: MessageStatus;
  /** Optional error text for failed UI messages. */
  error?: string;
  /** Original user content used by the retry affordance. */
  retry_content?: string;
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

/** A sub-agent record — v0.8.0 extended with icon, category, tags, etc. */
export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  system_prompt?: string;
  tool_allowlist?: string[];
  model?: string;
  // v0.8.0 extended fields
  icon?: string;
  color?: string;
  category?: string;
  tags?: string[];
  team_id?: string | null;
  skills?: string[];
  max_iterations?: number;
  temperature?: number | null;
}

/** Params for `agent.spawn_subagent` (extended in v0.3.0 §2). */
export interface SpawnSubagentParams {
  /** Stable human-readable lookup key used by the backend agents.name column. */
  agent_name?: string;
  /** Legacy/internal id. Kept for existing callers; backend now tolerates name or id. */
  agent_id: string;
  prompt: string;
  parent_session_id?: string;
  context_message_id?: string;
  display_name?: string;
  /** Client-generated run_id — passed to backend to avoid orphaned optimistic rows. */
  run_id?: string;
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

/** A buffered telemetry event from the in-memory observability bus (R11). */
export interface TelemetryEventRecord {
  type: string;
  session_id: string | null;
  severity: "info" | "warn" | "error";
  name: string | null;
  payload: Record<string, unknown>;
  ts: string;
}

/** Result shape for `telemetry.recent`. */
export interface TelemetryRecentResult {
  events: TelemetryEventRecord[];
  total: number;
  enabled: boolean;
  buffered: number;
}

/** One span in a trace (R14) — the flat shape buffered as a SPAN event. */
export interface TelemetrySpanRecord {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;
  duration_ms: number | null;
  status: "ok" | "error";
  error: string | null;
  start_ms: number;
  end_ms: number | null;
  attributes: Record<string, unknown>;
}

/** A span node in the reconstructed trace tree, with its children (R14). */
export interface TelemetrySpanNode extends TelemetrySpanRecord {
  children: TelemetrySpanNode[];
}

/** Result shape for `telemetry.trace` (R14). */
export interface TelemetryTraceResult {
  trace_id: string | null;
  spans: TelemetrySpanRecord[];
  tree: TelemetrySpanNode[];
  span_count: number;
  enabled: boolean;
}

/** Latency stats folded into `telemetry.metrics`. */
export interface TelemetryLatencyStats {
  count: number;
  avg: number;
  p50: number;
  p95: number;
  max: number;
}

/** One session's metrics roll-up (used in the all-sessions snapshot). */
export interface TelemetrySessionMetrics {
  session_id: string;
  session_starts: number;
  session_ends: number;
  turns: number;
  turn_completions: number;
  tool_calls: number;
  tool_errors: number;
  hook_fires: number;
  permissions: number;
  plugin_loads: number;
  errors: number;
  warnings: number;
  latency_ms: TelemetryLatencyStats;
}

/** Per-session / aggregate metrics — result of `telemetry.metrics` (R11). */
export interface TelemetryMetrics {
  enabled: boolean;
  /** Single-session fields (present when `session_id` is passed). */
  session_id?: string;
  session_starts?: number;
  session_ends?: number;
  turns?: number;
  turn_completions?: number;
  tool_calls?: number;
  tool_errors?: number;
  hook_fires?: number;
  permissions?: number;
  plugin_loads?: number;
  errors?: number;
  warnings?: number;
  latency_ms?: TelemetryLatencyStats;
  /** All-sessions roll-up fields (present when no `session_id`). */
  sessions?: number;
  per_session?: TelemetrySessionMetrics[];
  /** Always present. */
  global?: { errors: number; warnings: number };
  buffered?: number;
}

/** A previously-crashed run detected on boot (R12 marker-file protocol). */
export interface RuntimeCrashReport {
  crashed_pid: number;
  started_at: string;
  detected_at: string;
}

/** Snapshot of boot-time crash recovery — result of `runtime.recovery_status` (R12).
 *  `available:false` before recovery has run; `clean_start:true` when the
 *  previous boot exited cleanly and no orphan runs were recovered. */
export interface RuntimeRecoveryResult {
  available: boolean;
  /** Set once recovery has executed this boot. */
  clean_start?: boolean;
  /** True when the marker-file protocol detected an unclean exit. */
  previous_crash?: boolean;
  /** The consumed crash report (present when previous_crash is true). */
  crash?: RuntimeCrashReport | null;
  /** Number of in-flight runs flipped to failed. */
  recovered_runs?: number;
  run_ids?: string[];
  sessions?: string[];
  /** Present only when recovery could not import its deps (fail-open). */
  reason?: string;
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
  status:
    | "thinking"
    | "calling_tool"
    | "tool_running"
    | "tool_call"
    | "tool_result"
    | "done"
    | "max_iterations"
    | "idle"
    | "error";
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
  /** Tool name. Present on the Python agent stream; optional for older emitters. */
  name?: string;
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

/** Raw message row returned by `message.list` before UI normalization. */
export interface PersistedMessage {
  id: string;
  role: MessageRole;
  text?: string;
  content?: string;
  status?: MessageStatus;
  created_at: number;
  metadata?: MessageMetadata;
  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}

export type AgentRunStatus =
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentRunStepKind =
  | "thought"
  | "status"
  | "plan"
  | "tool_call"
  | "observation"
  | "approval"
  | "patch"
  | "final";

export type AgentRunStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface AgentRun {
  id: string;
  session_id: string;
  mode: "chat" | "plan" | "execute";
  status: AgentRunStatus;
  title: string;
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface AgentRunStep {
  id: string;
  run_id: string;
  session_id: string;
  kind: AgentRunStepKind;
  status: AgentRunStepStatus;
  title: string;
  summary: string;
  tool_call_id?: string | null;
  tool_name?: string | null;
  parent_id?: string | null;
  payload?: Record<string, unknown> | null;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
  ordinal: number;
}

export interface RunCreatedData {
  run: AgentRun;
}

export interface RunStepData {
  run_id: string;
  step: AgentRunStep;
}

export interface RunCompletedData {
  run: AgentRun;
}

export interface ListRunsResult {
  runs: AgentRun[];
}

export interface RunStepsResult {
  run: AgentRun;
  steps: AgentRunStep[];
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
  run_id?: string;
  text: string;
}

export interface ListSessionsResult {
  sessions: Session[];
  total?: number;
}

export interface CreateSessionResult {
  session_id: string;
  session?: Session;
  reused?: boolean;
  worktree_path?: string;
  base_branch?: string;
}

/** Return shape of `session.update`. */
export interface UpdateSessionResult {
  ok: boolean;
  session: Session;
}

/** Return shape of `session.updateProject`. */
export interface UpdateSessionProjectResult {
  ok: boolean;
  session: Session;
}

/** Return shape of `session.stats`. */
export interface SessionStatsResult {
  total_sessions: number;
  archived_sessions: number;
  total_messages: number;
}

/** Return shape of `session.export`. */
export interface SessionExportResult {
  markdown: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  archived: boolean;
  created_at: number;
  updated_at: number;
}

export interface ListProjectsResult {
  projects: Project[];
}

export interface CreateProjectResult {
  project: Project;
}

export interface UpdateProjectResult {
  ok: boolean;
  project: Project;
}

export interface ListMessagesResult {
  messages: PersistedMessage[];
}

/** Return shape of `message.update`. */
export interface UpdateMessageResult {
  ok: boolean;
  message: PersistedMessage;
}

/** Return shape of `message.delete`. */
export interface DeleteMessageResult {
  ok: boolean;
  message_id: string;
}

export interface ListModelsResult {
  models: ModelInfo[];
  current: string | null;
  /**
   * The persisted reasoning-effort override (R61 write-side read-back;
   * R63 frontend consumer). ``null`` / undefined means "no override — use
   * the selected model's own default effort" (the pre-R61 state). Mirrors
   * the ``reasoning_effort`` field the backend already returns on
   * ``model.list`` and ``model.get_current``.
   */
  reasoning_effort?: string | null;
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

/**
 * Result of ``model.set_reasoning_effort`` (R61 backend handler; R63 frontend
 * contract). ``reasoning_effort`` is the canonical wire token the backend
 * canonicalised + persisted (``"high"`` / ``"xhigh"`` / …) or ``null`` when
 * the override was cleared (revert to the model's own default effort). The
 * store echoes this into state so the switcher UI updates without a full
 * ``refresh()`` round-trip.
 */
export interface SetReasoningEffortResult {
  ok: true;
  reasoning_effort: string | null;
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

export type PatchLineKind = "context" | "add" | "delete" | "meta";

export interface PatchLine {
  kind: PatchLineKind;
  old_line: number | null;
  new_line: number | null;
  content: string;
}

export interface PatchHunk {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  header: string;
  lines: PatchLine[];
}

export type PatchFileStatus = "added" | "modified" | "deleted" | "renamed";

export interface PatchFile {
  path: string;
  old_path: string;
  new_path: string;
  status: PatchFileStatus;
  additions: number;
  deletions: number;
  binary: boolean;
  hunks: PatchHunk[];
}

export interface PatchStats {
  files: number;
  additions: number;
  deletions: number;
}

/** Return shape of `patch.preview`. */
export interface PatchPreviewResult {
  scope: string;
  ref?: string | null;
  diff: string;
  files: PatchFile[];
  stats: PatchStats;
}

export interface PatchHunkOperationParams {
  scope?: "working" | "staged";
  file_path: string;
  hunk_index: number;
  old_start?: number;
  new_start?: number;
}

export interface PatchHunkOperationResult {
  ok: true;
  operation: "apply_hunk" | "revert_hunk";
  scope: string;
  file_path: string;
  hunk_index: number;
}

export type TerminalStatus = "starting" | "running" | "completed" | "failed" | "cancelled";
export type TerminalStream = "stdout" | "stderr";

export interface TerminalSession {
  id: string;
  command: string;
  cwd: string;
  session_id?: string | null;
  run_id?: string | null;
  status: TerminalStatus;
  started_at: number;
  updated_at: number;
  completed_at: number | null;
  exit_code: number | null;
  error: string | null;
  next_seq: number;
}

export interface TerminalChunk {
  seq: number;
  stream: TerminalStream;
  text: string;
  received_at: number;
}

export interface TerminalStartResult {
  session: TerminalSession;
}

export interface TerminalReadResult {
  session: TerminalSession;
  chunks: TerminalChunk[];
}

export interface TerminalListResult {
  sessions: TerminalSession[];
}

export type RunnerKind = "native" | "external_cli";
export type RunnerSandboxMode = "read-only" | "workspace-write" | "danger-full-access";
export type RunnerApprovalPolicy = "untrusted" | "on-failure" | "on-request" | "never";
export type RunnerPermissionMode = "default" | "acceptEdits" | "bypassPermissions" | "plan";

export interface RunnerInfo {
  id: "native" | "codex-cli" | "claude-code-cli";
  label: string;
  kind: RunnerKind;
  available: boolean;
  command: string | null;
  version: string | null;
  reason: string | null;
  supports_prompt: boolean;
  supports_terminal: boolean;
}

export interface RunnerListResult {
  runners: RunnerInfo[];
}

export interface RunnerStartResult {
  runner: RunnerInfo;
  session: TerminalSession;
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

/* ─────────────────────── Crash recovery (R225-R230 consumption surface) ─────────────────────── */

/** Return shape of `crash.previous_report`. */
export interface CrashPreviousReportResult {
  /** `false` when there is no previous-crash report (no crash, or dismissed). */
  available: boolean;
  /** The human-readable rendered report; `null` when `available` is `false`. */
  report_text: string | null;
}

/** A single archived report returned by `crash.history`. */
export interface CrashHistoryEntry {
  /** Filename under `crashes/history/`, e.g. `crash-1719000000.txt`. */
  filename: string;
  /** Epoch-seconds parsed from the filename. */
  timestamp: number;
  /** The human-readable rendered report. */
  report_text: string;
}

/** Return shape of `crash.history`. */
export interface CrashHistoryResult {
  /** Newest-first (lexical filename order == chronological order). */
  entries: CrashHistoryEntry[];
}

/** Return shape of `crash.dismiss`. */
export interface CrashDismissResult {
  /** `true` when the previous-crash report was removed; `false` if absent or locked. */
  dismissed: boolean;
}

/* ─────────────────────── Sub-agent progress (§2 of v0.3.0 design) ─────────────────────── */

/** Lifecycle status of a single sub-agent run. */
export type SubAgentStatus =
  | "started"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "completed"
  | "failed"
  | "cancelled";

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
  NotificationNew: "notification.new",
  NotificationRead: "notification.read",
  TeamProgress: "agent.team_progress",
  RunCreated: "run.created",
  RunStepStarted: "run.step.started",
  RunStepCompleted: "run.step.completed",
  RunCompleted: "run.completed",
} as const;

export type StreamEventName = (typeof StreamEvent)[keyof typeof StreamEvent];

/** Shape for the optional sidecar status event from Rust. */
export interface SidecarEvent {
  status: "started" | "stopped" | "error";
  error?: string;
}

// ---------------------------------------------------------------------------
// v0.7.0 — Notifications
// ---------------------------------------------------------------------------

/** A single notification entry from the notification centre. */
export interface NotificationEntry {
  id: string;
  type: string;
  title: string;
  body: string;
  source?: string;
  source_id?: string;
  priority: number;
  read: boolean;
  created_at: string;
  read_at?: string;
}

/** Result of ``notification.list`` IPC call. */
export interface ListNotificationsResult {
  entries: NotificationEntry[];
  total: number;
}

// ---------------------------------------------------------------------------
// v0.7.0 — Workflows
// ---------------------------------------------------------------------------

/** A single workflow step — condition branch or action execution. */
export interface WorkflowStep {
  type: "condition" | "action";
  /** For condition steps: the if-clause. */
  if?: { field: string; op: "eq" | "neq" | "contains" | "startswith"; value: string };
  then_step?: number;
  else_step?: number;
  /** For action steps: what action to execute. */
  action_type?: "notify" | "send-message" | "code-review" | "run-skill";
  config?: Record<string, unknown>;
}

/** A workflow entry — matches `workflows` table row. */
export interface WorkflowEntry {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  trigger_type: "webhook" | "schedule" | "agent_event";
  trigger_config: Record<string, unknown>;
  steps: WorkflowStep[];
  last_run_at: string | null;
  run_count: number;
  created_at: string;
  updated_at: string;
}

/** Result of ``workflow.list`` IPC call. */
export interface ListWorkflowsResult {
  entries: WorkflowEntry[];
  total: number;
}

// ---------------------------------------------------------------------------
// v0.6.0 — Code Completion
// ---------------------------------------------------------------------------

/** Request body for POST /complete (not an IPC method — direct HTTP). */
export interface CompletionRequest {
  file_path: string;
  content_before?: string;
  content_after?: string;
  language?: string;
  max_tokens?: number;
  temperature?: number;
}

/** Response from POST /complete. */
export interface CompletionResponse {
  text: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
}

// ---------------------------------------------------------------------------
// v0.6.0 — Multimodal Content Parts
// ---------------------------------------------------------------------------

export interface ContentPartText {
  type: "text";
  text: string;
}

export interface ContentPartImage {
  type: "image";
  media_type: string;
  data: string; // base64-encoded
}

export type ContentPart = ContentPartText | ContentPartImage;

// ---------------------------------------------------------------------------
// v0.8.0 — Agent Teams (Enterprise Multi-Agent)
// ---------------------------------------------------------------------------

/** Orchestration modes for an agent team. */
export type OrchestrationMode = "parallel" | "sequential" | "round-robin";

/** An agent team template — matches `agent_teams` table row. */
export interface AgentTeam {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  agents: string[]; // array of agent names
  orchestration_mode: OrchestrationMode;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** Result of ``team.list`` IPC call. */
export interface ListTeamsResult {
  teams: AgentTeam[];
}

/** Team progress event payload — pushed via WebSocket during team.spawn. */
export interface TeamProgressData {
  team_name: string;
  task_id: string;
  status: "started" | "agent_started" | "agent_completed" | "completed" | "failed";
  agent_name?: string;
  progress: number; // 0..1
  summary?: string;
  agents_completed?: number;
  agents_total?: number;
}

// ---------------------------------------------------------------------------
// Plugins (platform pillar #3) — runtime-extensible plugin registry.
// ---------------------------------------------------------------------------

/** A discovered plugin's info record — mirrors the Python `Plugin` wire shape. */
export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  author: string;
  homepage: string;
  /** Effective enabled state (runtime override wins over manifest). */
  enabled: boolean;
  /** On-disk manifest enabled flag (before any runtime override). */
  enabled_on_disk: boolean;
  /** False when the manifest failed to parse (fail-open discovery). */
  ok: boolean;
  error: string | null;
  path: string;
  loaded_at: string;
  has_hooks: boolean;
  has_mcp: boolean;
  has_permissions: boolean;
  entry: string | null;
}

/** Optional filters accepted by ``plugins.list``. */
export interface ListPluginsParams {
  /** Include plugins whose manifest failed to parse (default true). */
  include_failed?: boolean;
  /** Only return effectively-enabled plugins. */
  enabled_only?: boolean;
}

/** Result of ``plugins.list`` IPC call. */
export interface ListPluginsResult {
  plugins: PluginInfo[];
  total: number;
}

/** Result of ``plugins.info`` IPC call. */
export interface PluginInfoResult {
  plugin: PluginInfo;
}

/** Result of ``plugins.enable`` / ``plugins.disable``. */
export interface PluginToggleResult {
  ok: true;
  name: string;
  enabled: boolean;
}

/** Result of ``plugins.reload``. */
export interface PluginReloadResult {
  ok: true;
  total: number;
  reloaded: number;
  failed: number;
  plugins: PluginInfo[];
}
