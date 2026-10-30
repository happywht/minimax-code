/**
 * JSON-RPC 2.0 envelope types + shared protocol types — must match
 * `agent/minimax_code/ipc/protocol.py`.
 *
 * The Rust sidecar re-emits the raw JSON; the frontend is responsible
 * for type-narrowing with these interfaces.
 */

export type JsonRpcId = string | number | null;

export interface TextArtifact {
  id: string;
  project_id: string;
  run_id: string | null;
  service_id: string;
  source_path: string;
  snapshot_id: string;
  mime_type: "text/plain" | "text/markdown" | "text/csv";
  size_bytes: number;
  sha256: string;
  version: number;
  parse_status: "parseable";
  verification_status: "unverified";
  created_at: string;
}

export interface ArtifactRegisterParams {
  project_id: string;
  source_path: string;
  run_id?: string;
  expected_service_id?: string;
  expected_session_id?: string;
  request_id?: string;
}

export interface ArtifactListParams { project_id: string; run_id?: string; cursor?: string; query?: string }
export interface ArtifactListResult { artifacts: TextArtifact[]; service_id: string; next_cursor?: string | null }
export interface ArtifactGetResult { artifact: TextArtifact; preview_text: string }

export type AttachmentKind = "txt" | "csv" | "pdf" | "docx" | "xlsx" | "png" | "jpeg" | "gif" | "webp" | "unknown";
export type AttachmentParserStatus = "parsed" | "empty" | "truncated" | "parse_failed" | "type_mismatch" | "parser_unavailable" | "ocr_not_attempted";
export type AttachmentParserReason = "legacy_v1" | "ok" | "no_extracted_body_text_no_ocr" | "preview_limit" | "invalid_package" | "unsafe_package" | "package_limit" | "unsupported_feature" | "missing_dependency" | "limits_unavailable" | "queue_timeout" | "parser_timeout" | "parser_crash" | "output_limit" | "invalid_parser_output" | "invalid_text" | "type_mismatch" | "unsupported_type" | "ocr_not_attempted";
export interface AttachmentOriginal {
  id: string;
  project_id: string;
  service_id: string;
  snapshot_id: string;
  original_name: string;
  declared_type: AttachmentKind;
  recognized_type: AttachmentKind | "utf8_text" | "zip" | "binary";
  size_bytes: number;
  sha256: string;
  version: number;
  parser_status: AttachmentParserStatus;
  parser_version: 1 | 2;
  parser_reason: AttachmentParserReason;
  /** inert_text includes bounded PDF text in physical page order, without OCR. */
  parser_scope: "legacy_v1" | "inert_text" | "docx_body_paragraphs_tables" | "original_only";
  preview_truncated: boolean;
  quality_status: "unverified";
  created_at: string;
}
export interface AttachmentListResult { attachments: AttachmentOriginal[]; service_id: string; next_cursor?: string | null }
export type DocxBlock = { kind: "paragraph"; text: string } | { kind: "table"; rows: string[][]; repeat_header?: boolean };
export interface DocxSection { heading: string; blocks: DocxBlock[] }
export interface ReportSource { title: string; url: string; accessed_at?: string; excerpt?: string | null; excerpt_truncated?: boolean; title_truncated?: boolean }
export interface PageExcerptRange { page_source_id: string; start: number; end: number }
export interface DocumentExcerptRef { attachment_id: string; expected_sha256: string; unit: number; start: number; end: number }
export type DocxContent = ({ paragraphs: string[]; sections?: never } | { sections: DocxSection[]; paragraphs?: never }) & { sources?: ReportSource[]; session_id?: string; page_source_ids?: string[]; page_excerpt_ranges?: PageExcerptRange[]; document_excerpts?: DocumentExcerptRef[] };
export type AttachmentGenerateDocxParams = {
  project_id: string; expected_service_id: string; filename: string; title: string;
} & DocxContent;
export interface AttachmentGetResult { attachment: AttachmentOriginal; preview_text: string }
export type ReportFormat = "docx" | "pdf";
export type AttachmentReportPairParams = AttachmentGenerateDocxParams & { formats?: ReportFormat[] };
export type ReportPairItem = { format: ReportFormat; status: "saved"; attachment: AttachmentOriginal; preview_text: string }
  | { format: ReportFormat; status: "unconfirmed"; error: string };
export interface ReportPairResult { status: "complete" | "partial" | "unconfirmed"; results: ReportPairItem[] }


export interface OfficeTableSpec {
  sheet_name: string;
  columns: { name: string; kind: "text" | "number" | "boolean" }[];
  rows: (string | number | boolean | null)[][];
}
export interface OfficeReport {
  version: 1;
  scope: "xlsx_plain_cells_v1" | "xlsx_chart_values_v1";
  input_sha256: string;
  file_sha256: string;
  size_bytes: number;
  sheet_name: string;
  data_rows: number;
  column_count: number;
  cell_count: number;
  formula_count?: 0;
  external_relationship_count?: 0;
  macro_count?: 0;
  structure_status?: "passed";
  cell_check?: "passed";
  chart_reference_check?: "passed";
  business_quality: "unverified";
  render_status: "not_attempted";
  writer: { name: "openpyxl"; version: "3.1.5" };
  validator_version: 1;
  processing?: Record<string, unknown>;
  operator_notes?: OfficeOperatorNotes;
}
export interface OfficeDelivery {
  id: string;
  project_id: string;
  service_id: string;
  attachment_id: string;
  run_id: string | null;
  request_key: string;
  request_hash: string;
  input_sha256: string;
  writer_version: "3.1.5";
  validator_version: 1;
  validation_report: OfficeReport;
  created_at: string;
}
export interface OfficeEntry { delivery: OfficeDelivery; attachment: AttachmentOriginal }
export interface OfficeGetResult extends OfficeEntry { preview_text: string }
export interface OfficeListResult { deliveries: OfficeEntry[]; service_id: string; next_cursor: string | null }
export interface OfficeGenerateParams {
  project_id: string;
  expected_service_id: string;
  request_key: string;
  filename: string;
  run_id?: string | null;
  spec: OfficeTableSpec;
  operator_notes?: OfficeOperatorNotes;
}
export interface OfficeOperatorNotes { source: string; method: string; limitations: string }
export interface OfficeSummary { column: string; operation: "sum" | "count" | "average"; decimals: number }
export interface OfficeChart { kind: "bar" | "line"; category_column: string; value_column: string; title: string }
export type OfficeAnalysisParams = OfficeGenerateParams & { summaries: OfficeSummary[]; chart: OfficeChart | null; register_chart_delivery?: boolean };
export type OfficeAnalysisResult = ({ kind: "delivery"; materialized_spec: OfficeTableSpec;
  analysis_spec?: { spec: OfficeTableSpec; chart: OfficeChart; source_rows: number } } & OfficeGetResult) |
  { kind: "attachment"; attachment: AttachmentOriginal; validation_report: { scope: "xlsx_chart_values_v1"; input_sha256: string;
    file_sha256: string; size_bytes: number; cell_check: "passed"; chart_reference_check: "passed"; render_status: "not_attempted" };
    analysis_spec: { spec: OfficeTableSpec; chart: OfficeChart; source_rows: number } };

/** SSH credentials are referenced by identity-file path, never embedded. */
export interface HostConfig {
  name: string;
  hostname: string;
  username: string;
  port?: number;
  identity_file?: string;
  remote_port?: number;
}

export interface HostConnection {
  host_id: string;
  state: "connected" | "degraded" | "disconnected" | "failed" | "connecting" | "reconnecting";
  local_url: string | null;
  remote_port: number | null;
  error: string | null;
}

export interface RemoteHost extends HostConfig {
  id: string;
  remote_port: number;
  port: number;
  identity_file: string;
  enabled: boolean;
  agent_version: string;
  created_at: string;
  updated_at: string;
  last_probe_at: string | null;
  last_probe_ok: boolean | null;
}

export interface HostBindingParams {
  host_id: string; expected_service_id: string; hostname: string; username: string;
  port: number; identity_file: string; remote_port: number;
}

export interface HostDirectoryListParams extends HostBindingParams { remote_path: string; offset: number }
export interface HostDirectoryListing {
  path: string; entries: { name: string; kind: "directory" | "file" }[];
  offset: number; next_offset: number | null; limited: boolean;
}

export interface HostBoundFetchParams extends HostBindingParams { remote_path: string; local_path: string }
export type HostBoundFetchResult =
  | { ok: true; reason: null; sha256: string; size_bytes: number }
  | { ok: false; reason: "exists"; sha256: null; size_bytes: null };

export interface HostFetchPreviewParams extends HostBindingParams {
  remote_path: string; local_path: string; expected_sha256: string;
}

export interface HostServiceStartParams extends HostBindingParams {
  unit: string;
}

export interface HostServiceLogsParams extends HostServiceStartParams {
  lines?: 25 | 50 | 100 | 200;
  since?: "all" | "15m" | "1h" | "24h" | "7d";
}

export interface HostServiceStopParams extends HostServiceStartParams {
  acknowledge_interrupt: true;
}

export interface HostServiceStatus {
  LoadState: string; ActiveState: string; SubState: string; Result: string;
  ExecMainStatus: string; MainPID: string; UnitFileState?: string;
}
export type HostServiceStatusResult =
  | { ok: true; reason: null; status: HostServiceStatus }
  | { ok: false; reason: "timeout" | "unavailable"; status: null };

export type HostServiceLogsResult =
  | { ok: true; reason: null; text: string; truncated: boolean }
  | { ok: false; reason: "timeout" | "unavailable_or_empty"; text: null; truncated: false };

export interface HostPushParams {
  host_id: string;
  local_path: string;
  remote_path: string;
  /** Pre-record a fresh 32-character lowercase hex ID to query after cancellation. */
  transfer_id?: string;
}

export interface HostBoundTransferStatusParams {
  host_id: string; expected_service_id: string; hostname: string; username: string;
  port: number; identity_file: string; remote_port: number; remote_path: string; transfer_id: string;
}

export interface HostTransferStatusParams {
  host_id: string;
  remote_path: string;
  transfer_id: string;
}

export type UploadPublicationState = "staged" | "publishing" | "published" | "aborted" | "unknown" | "not_published";

export interface HostTransferResult {
  ok: boolean;
  exit_code: number | null;
  stderr: string;
  remote_path: string;
  transfer_id: string | null;
  publication_state: UploadPublicationState | null;
  backup_path: string | null;
}

export interface HostExecResult {
  ok: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  timed_out: boolean;
}

export interface HostProbeResult {
  probe: {
    ok: boolean;
    timed_out: boolean;
    agent_version: string;
    os: string;
    python_version: string;
    codex_present?: boolean;
    error?: string;
  };
  host: RemoteHost;
}

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
  /**
   * Monotonic sequence number stamped by the agent on WS broadcasts
   * (v0.13.0). A client reconnecting with `GET /ws?since=<last seq>`
   * receives the missed events replayed in order. Lifecycle frames
   * (`agent.ready`, `agent.ping`) carry no seq and are never replayed.
   */
  seq?: number;
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
  engine?: "native" | "codex";
  engine_thread_id?: string | null;
  engine_state?: { version: number; workspace?: string; protocol?: string; handoff_required?: boolean; [key: string]: unknown } | null;
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
  | "cancelled"
  | "budget_paused"
  | "interrupted";

export interface Message {
  id: string;
  /** Browser-only row identity while a provisional ID is replaced by a saved ID. */
  render_key?: string;
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

/** A single codebase source annotation attached to a message. */
export interface SourceAnnotation {
  file_path: string;
  line_range: string | null;
}

/** Per-turn metadata snapshot for the v0.3.0 thinking_count channel. */
export interface MessageMetadata {
  verification?: VerificationEvidence;
  run_status?: AgentRunStatus;
  engine?: "native" | "codex";
  external_turn_id?: string | null;
  partial?: boolean;
  status?: string;
  usage_certainty?: "unknown" | "observed" | "uncertain" | "recovery_interval";
  /** Browser-only association until a submitted user message is persisted. */
  request_key?: string;
  /** Originating durable run, absent on legacy/imported messages. */
  run_id?: string;
  thinking_count?: number;
  tokens_in?: number | null;
  tokens_out?: number | null;
  /** v0.11.0: sources harvested from codebase tools used in this turn. */
  sources?: SourceAnnotation[];
  /** v0.11.0: number of long-term memories auto-extracted from this reply. */
  memory_count?: number;
  /**
   * v1.1.0: the turn hit its iteration budget without a final answer.
   * Drives the "continue" affordance on the message.
   */
  truncated?: boolean;
  /** v1.1.0: how many intra-loop compactions ran during this turn. */
  compactions?: number;
}

/** A scheduled job record. */
export interface ScheduledJob {
  id: string;
  name: string;
  cron: string;
  prompt: string;
  enabled: boolean;
  last_run_at: string | number | null;
  next_run_at: string | number | null;
  revision?: number;
  context?: ScheduleContext | null;
  binding_state?: "ready" | "needs_rebind" | "blocked" | "requires_review";
  binding_reason?: string | null;
  active_occurrence_id?: string | null;
  deleted_at?: string | null;
}

export interface ScheduleContext {
  hooks_policy?: "disabled";
  version?: number;
  host?: "local";
  engine?: "native";
  project_id: string;
  workspace?: string;
  provider_id: string;
  model: string;
  reasoning_effort?: string | null;
  mode: "chat" | "execute" | "plan";
  budget_tokens: number;
  timezone: string;
  restart_missed_policy?: "coalesce" | "skip";
  validate_command?: string;
  permission_rules?: { tool_pattern: string; action: string }[];
}

export interface ScheduleDefinition { name: string; cron: string; prompt: string; context: ScheduleContext }
export interface SchedulePreview { service_id: string; cron_expr: string; timezone: string; calculated_at: string; times: string[] }
export interface ScheduleOccurrence {
  id: string; job_id: string; revision: number; source: "cron" | "manual";
  session_id: string; task_id: string; run_id: string | null; status: string;
  reason?: string | null; scheduled_for?: string | null; coalesced_through?: string | null;
  created_at: string; context: ScheduleContext;
  verification?: VerificationEvidence;
  execution_status?: string;
  result?: string | null;
}
export interface ScheduleOccurrenceCursor { created_at: string; id: string }
export interface ScheduleTriggerResult { ok: true; job_id: string; occurrence_id: string; task_id: string; run_id: string | null; status: string; created: boolean }

/** A skill record. */
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
  /** Manifest requirements, when available from the skill catalogue. */
  tools?: string[];
  version?: string;
}

/**
 * Reply from `skill.invoke` — mirrors handlers_skills.py verbatim.
 * `text` and `output` carry the same final reply; `output` is the
 * canonical channel the UI reads. `comments`/`stats` ride along only
 * on the code-review diff route.
 */
export interface SkillInvokeArgs {
  request: string;
  session_id: string;
  idempotency_key: string;
  expected_service_id?: string;
  expected_project_id?: string | null;
  diff?: string;
  model?: string;
  max_iterations?: number;
  budget_tokens?: number;
  expected_workspace?: string;
  label?: "overview" | "security" | "performance" | "style";
}
export interface SkillInvokeResult {
  session_id: string;
  message_id: string;
  skill_id: string;
  text: string;
  output: string;
  iterations: number;
  tool_calls: number;
  cancelled: boolean;
  truncated: boolean;
  budget_exhausted?: boolean;
  run_id: string;
  status: AgentRunStatus;
  run_status: "completed" | "failed" | "cancelled" | "budget_paused" | "interrupted";
  partial: boolean;
  error?: { message: string };
  verification?: VerificationEvidence;
  /** Code-review diff route only — severity is free-form; narrow at the call site. */
  comments?: Array<{
    file: string;
    line: number | null;
    severity: string;
    message: string;
  }>;
  /** Code-review diff route only. */
  stats?: { files: number; additions: number; deletions: number };
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
  /** null/missing: root uses host; trusted descendants inherit the parent. */
  execution_profile_id?: string | null;
  // v0.8.0 extended fields
  icon?: string;
  color?: string;
  category?: string;
  tags?: string[];
  team_id?: string | null;
  /** Ordered dependencies: exact skill ID, or an unambiguous legacy short name. */
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
  session_id: string;
  idempotency_key: string;
  model?: string;
  budget_tokens?: number;
  parent_session_id?: string;
  context_message_id?: string;
  display_name?: string;
  /** Client association only; the server always allocates the durable run ID. */
  run_id?: string;
}

/** A permission rule. */
export interface PermissionRule {
  id: string;
  tool: string;
  pattern: string;
  decision: "allow" | "deny" | "ask";
  created_at: number;
  /** Present (==="default") on factory-default rules — shown by the
   * Settings UI and not deletable (only overridable). */
  origin?: "default";
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
export interface ResearchConfigResult {
  saved_url: string | null;
  effective_url: string | null;
  source: "settings" | "environment" | "environment_invalid" | "unconfigured";
  provider?: "searxng" | "brave";
  brave_key_configured?: boolean;
}

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

/** Durable run identity lets clients discard late notifications after recovery. */
export interface RunStreamContext {
  session_id: string;
  run_id?: string;
}

/** Pin continuation/retry to a particular truncated block. */
export interface ContinueRunParams {
  session_id: string;
  run_id?: string;
}

export interface MessageChunkData extends RunStreamContext {
  message_id: string;
  delta: string;
  done: boolean;
  stream_seq?: number;
  stream_unavailable?: boolean;
  stream_event_seq?: number;
  /**
   * Optional per-turn metadata — populated on the trailing
   * ``done=True`` chunk (and possibly the first text chunk) by
   * the v0.3.0 ``thinking_count`` wire format. Earlier chunks in
   * the same turn omit the field; the store keeps the latest
   * non-null value per message so the UI sees a stable snapshot
   * even before the stream ends. v0.11.0 adds ``metadata.sources``
   * for codebase tool source annotations.
   */
  metadata?: MessageMetadata;
}

export interface AgentStatusData extends RunStreamContext {
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

export interface ToolCallData extends RunStreamContext {
  tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
  message_id: string;
}

export interface ToolResultData extends RunStreamContext {
  tool_call_id: string;
  /** Tool name. Present on the Python agent stream; optional for older emitters. */
  name?: string;
  result: unknown;
  error?: string;
  message_id: string;
}

/** One selectable option of an `ask_user` question. */
export interface AskUserOption {
  label: string;
  description?: string;
}

/** One question raised by the agent via the `ask_user` tool. */
export interface AskUserQuestion {
  question: string;
  header: string;
  options: AskUserOption[];
  multiSelect: boolean;
  id?: string;
  isOther?: boolean;
  isSecret?: boolean;
}

/** Wire shape of one `agent.ask_user` event — the agent is suspended until answered. */
export interface AskUserData extends RunStreamContext {
  request_id: string;
  questions: AskUserQuestion[];
  /** Seconds the agent will wait before giving up (default 600). */
  timeout_s: number;
  expires_at_ms?: number;
  remaining_ms?: number;
  tool_call_id?: string | null;
  /** Name of the child agent asking on behalf of the owning run. */
  agent_name?: string;
  /** Separate session whose core owns this child's question. */
  child_session_id?: string;
  engine?: "codex";
  is_blocking?: boolean;
}

/** Result shape for `agent.answer_user`. */
export interface AnswerUserResult {
  ok: boolean;
  request_id?: string;
  error?: string;
}

export interface PermissionRequestData {
  approval_policy?: "explicit_once";
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
  /** v1.2.0 — the conversation the gated tool call belongs to (omitted
   *  when the gater has no session context, e.g. bare CLI runs). */
  session_id?: string;
  run_id?: string;
  remaining_ms?: number;
}

export interface PermissionResolvedData {
  request_id: string;
  decision: "allow" | "deny";
}

export interface TaskProgressData {
  task_id: string;
  progress: number; // 0..1
  message?: string;
  status: "running" | "done" | "error" | "cancelled" | "completed" | "failed" | "budget_paused" | "interrupted" | "requires_review";
  run_id?: string | null;
  execution_status?: string | null;
  verification?: VerificationEvidence;
  result?: string | null;
}

/** Raw message row returned by `message.list` before UI normalization. */
export interface PersistedMessage {
  id: string;
  role: MessageRole;
  text?: string;
  content?: string;
  status?: MessageStatus;
  streaming?: boolean;
  created_at: number;
  metadata?: MessageMetadata;
  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}

export type AgentRunStatus =
  | "queued"
  | "waiting_input"
  | "budget_paused"
  | "interrupted"
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

export interface NativeModelSnapshot {
  engine: "native";
  provider_id: string | null;
  model: string;
  protocol: string | null;
  reasoning_effort: string | null;
  context_window: number;
}

export interface CodexPlan {
  explanation: string | null;
  steps: Array<{ step: string; status: "pending" | "inProgress" | "completed" }>;
  truncated: boolean;
}

export interface AgentRun {
  id: string;
  session_id: string;
  mode: "chat" | "plan" | "execute" | "team" | "subagent";
  status: AgentRunStatus;
  title: string;
  request_key?: string | null;
  resumed_from_id?: string | null;
  user_message_id?: string | null;
  assistant_message_id?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  metadata?: (Record<string, unknown> & { model_snapshot?: NativeModelSnapshot | null; codex_plan?: CodexPlan;
    codex_usage?: { input_tokens: number; output_tokens: number; total_tokens: number } | null;
    usage_certainty?: "unknown" | "observed" | "uncertain" | "recovery_interval" }) | null;
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

export interface ProjectRunSummaryItem {
  id: string; session_id: string; title: string; session_title: string; status: AgentRunStatus;
  created_at: string; completed_at: string | null; live: boolean | null;
}
export type ProjectRunHistoryStatus = "all" | "attention" | "active" | "failed" | "completed";

export interface ProjectRunSummaryResult {
  project_id: string; service_id: string;
  groups?: Record<"attention" | "active" | "recent", { items: ProjectRunSummaryItem[]; truncated: boolean }>;
  detail?: Omit<ProjectRunSummaryItem, "live"> & { text: string | null; text_truncated: boolean };
  history?: { items: ProjectRunSummaryItem[]; offset: number; has_more: boolean };
}

export interface RunRecoveryLookup {
  run_id: string;
  idempotency_key: string;
  session_id: string;
  project_id: string | null;
  service_id: string;
}

export interface ListRunsResult {
  runs: AgentRun[];
  active_truncated?: boolean;
}

export interface RunStepsResult {
  run: AgentRun;
  steps: AgentRunStep[];
  cursor: number;
  queued_input?: { text: string; truncated: boolean } | null;
  pending_ask_user?: AskUserData | null;
  pending_ask_users?: AskUserData[];
  pending_native_permissions?: PermissionRequestData[];
  pending_codex_permissions?: PermissionRequestData[];
  live_stream?: { complete: boolean; messages: { message_id: string; text: string; seq: number; done: boolean }[] } | null;
}

export interface ResearchSource {
  page_source_id: string; run_id: string; title: string; url: string; accessed_at: string;
  excerpt: string | null; title_truncated: boolean; excerpt_truncated: boolean; truncated?: boolean;
  excerpt_match?: { text: string; start: number; end: number; match_start: number; match_end: number };
}
export interface ResearchSourceScope { session_id: string; project_id: string; service_id: string }

export interface RunEvent {
  id: string;
  run_id: string;
  seq: number;
  type: "created" | "status" | "step_created" | "step_updated" | "recovered" | "budget";
  created_at: string;
  data: { run?: AgentRun; step?: AgentRunStep; [key: string]: unknown };
}

export interface BudgetDay {
  day: string;
  known_tokens: number;
  reserved_tokens: number;
  unknown_attempts: number;
  baseline_incomplete: boolean;
  historical_lower_bound: number;
  historical_liability: number | null;
  // Older services omit these fields; absence is unknown, never complete.
  accounting_resolved?: boolean;
  usage_complete?: boolean;
}

export interface DurableBudget {
  family_id: string;
  limit_tokens: number | null;
  known_tokens: number;
  reserved_tokens: number;
  unknown_attempts: number;
  remaining_estimate: number | null;
  enforcement: "estimated" | "reactive";
  pause_reason: string | null;
  baseline_incomplete: boolean;
  usage_complete: boolean;
  accounting_resolved?: boolean;
  daily_limit: number | null;
  day: BudgetDay;
  assumptions: string;
  price: null;
  currency: null;
}

export interface BudgetStateResult {
  budget: DurableBudget | null;
  day: BudgetDay;
  attempts: Array<{id: string; run_id?: string | null; state: string; estimated_tokens: number; known_tokens: number | null; liability_tokens: number | null; reason: string | null}>;
  coverage_started_at: string | null;
  day_blockers?: {total: number; next_cursor?: number | null; items: Array<{attempt_id: string; family_id: string; known_tokens: number | null; run_id: string | null; session_id: string | null}>};
}

export interface DailyBudgetSettings {
  service_id: string; saved_limit: number | null; effective_limit: number | null;
  source: "settings" | "environment" | "unconfigured"; day: BudgetDay; timezone: "Asia/Shanghai";
}

export interface BudgetReconcileParams {
  target: "day" | "family" | "attempt";
  target_id: string;
  liability_tokens: number;
  reason: string;
  acknowledged: true;
}

export interface RunEventsResult {
  events: RunEvent[];
  next_seq: number;
  has_more: boolean;
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

export interface SendMessageParams {
  capture_file_changes?: boolean;
  restrict_network_tools?: boolean;
  session_id: string | null;
  content: string | ContentPart[];
  idempotency_key?: string;
  mode?: "chat" | "plan" | "execute";
  /** M2 — per-run token budget; omit defers to the env default, 0 disables. */
  budget_tokens?: number;
}

export interface RunStartParams extends SendMessageParams {
  session_id: string;
  idempotency_key: string;
  expected_service_id?: string;
  engine?: "native" | "codex";
}

export interface RunInputImage {
  run_id: string;
  session_id: string;
  project_id: string | null;
  service_id: string;
  index: number;
  count: number;
  media_type: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  data: string;
}

export interface RunInputText {
  run_id: string;
  session_id: string;
  project_id: string | null;
  service_id: string;
  cursor: number;
  text: string;
  truncated: boolean;
  image_count: number;
}

export interface RunAdmissionResult { run: AgentRun; created: boolean }
export interface RunGetResult {
  run: AgentRun;
  result: SendMessageResult | { error: { code: number; message: string; data?: unknown } } | null;
}

export interface SendMessageResult {
  session_id: string;
  message_id: string;
  run_id?: string;
  text: string;
  /** Codex terminal evidence. Missing usage is unknown, not zero. */
  tokens_in?: number | null;
  tokens_out?: number | null;
  run_status?: "completed" | "failed" | "interrupted" | "cancelled" | "budget_paused";
  external_turn_id?: string | null;
  usage_certainty?: "unknown" | "observed" | "uncertain" | "recovery_interval";
  tokens_total?: number | null;
  error?: { message: string; [key: string]: unknown };
  /** Cancellation is confirmed after the service-owned task has unwound. */
  cancelled?: boolean;
  /** v1.1.0: the turn ended budget-truncated (no final answer). */
  truncated?: boolean;
  /** M2 — the run paused at a call boundary on its token budget. */
  budget_paused?: boolean;
  /** M2 — budget snapshot at the end of the run (null when unbudgeted). */
  budget?: DurableBudget | { limit_tokens: number | null; daily_limit: number | null; used_tokens: number; pauses: number } | null;
  /**
   * M2 — acceptance evidence. Failed tool calls mean the verbal "done"
   * is not a verified completion (运行结束 ≠ 验收通过).
   */
  verification?: VerificationEvidence;
  iterations?: number;
  /** v1.1.0: blocks ran behind this send (auto-continue; 1 when off). */
  blocks?: number;
  /** v1.1.0: intra-loop compactions summed across blocks. */
  compactions?: number;
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

/** Return shape of `session.batchArchive`. */
export interface BatchArchiveSessionsResult {
  ok: boolean;
  session_ids: string[];
  sessions: Session[];
  updated: number;
}

/** Return shape of `session.batchUpdateProject`. */
export interface BatchUpdateSessionProjectResult {
  ok: boolean;
  project_id: string;
  session_ids: string[];
  sessions: Session[];
  updated: number;
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
  /** v1.3.0: absolute workspace root this project is anchored at ("" = unrooted). */
  root_path: string;
  /**
   * M3: command that proves this project's code is good (e.g. `pytest -x`).
   * Runs that modify files execute it and attach the result to the run's
   * acceptance evidence ("" = no automatic validation).
   */
  validate_command: string;
  archived: boolean;
  created_at: number;
  updated_at: number;
}

export interface ListProjectsResult {
  projects: Project[];
}

/** M4 — capability card for one agent engine (native / Codex). */
export interface EngineInfo {
  id: string;
  name: string;
  available: boolean;
  /** Why this engine cannot be used right now (null when available). */
  unavailable_reason: string | null;
  /** streaming / resume / cancel / approvals / usage / steer. */
  capabilities: Record<string, boolean>;
  version: string | null;
}

export interface ListEnginesResult {
  engines: EngineInfo[];
  default: string;
}

export interface EngineStatusResult {
  engine: EngineInfo;
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
  execution_profiles: ExecutionProfileCapability[];
}

export interface ExecutionProfileCapability {
  id: string;
  label: string;
  mode: "host" | "podman";
  available: boolean;
  reason: string | null;
  unattended_supported: false;
  boundary_summary: string;
}

export type ExecutionTarget = {
  version: 1;
  policy_version: 1;
  mode: "host";
  profile_id: "native-host";
} | {
  version: 1;
  policy_version: 2;
  mode: "podman";
  profile_id: "linux-podman-l1";
  image_id: string;
  memory_bytes: number;
  cpus_millis: number;
  pids_limit: number;
  timeout_seconds: number;
  tmpfs_bytes: number;
  output_limit: number;
  governor: { kind: "pid1-boottime-v1"; guard_sha256: string; seccomp_sha256: string };
};

export interface ListJobsResult {
  jobs: ScheduledJob[];
}

/** Snapshot of the API key state — return shape of `secrets.status`. */
export interface SecretStatus {
  configured: boolean;
  source: "keyring" | "env" | "none";
}

/**
 * Data-portability envelope — return shape of `data.export` and the
 * request payload of `data.import` (R21/R22). One JSON document holds
 * every business table; `schema_version` gates imports from newer
 * schemas, and `counts` mirrors the per-table row totals.
 */
export interface DataExportEnvelope {
  format: "minimax-code-export";
  schema_version: number;
  app_version: string;
  exported_at: string;
  counts: Record<string, number>;
  tables: Record<string, Array<Record<string, unknown>>>;
}

/** Replace-import summary — return shape of `data.import` (R22). */
export interface DataImportSummary {
  imported: Record<string, number>;
  skipped_tables: string[];
}

/** Backup snapshot result — return shape of `data.backup` (R23). */
export interface DataBackupResult {
  path: string;
  bytes: number;
}

/** File metadata only; presence does not establish backup integrity. */
export interface DataBackupList {
  directory: string;
  items: { path: string; bytes: number; modified_at: string }[];
  truncated: boolean;
}

/**
 * Sanitized diagnostic bundle — return shape of `diag.export` (R45).
 * Safe to attach to a bug report by construction: config carries enums /
 * numbers / booleans / counts only (never secret values), paths are
 * reduced to basenames, and the log tail is redacted agent-side.
 */
export interface DiagnosticBundle {
  format: "minimax-code-diagnostic";
  generated_at: string;
  version: string;
  platform: {
    system: string;
    release: string;
    machine: string;
    python: string;
    pid: number;
  };
  runtime: { uptime_s: number };
  config: Record<string, string | number | boolean | null>;
  storage:
    | {
        db_available: true;
        table_count: number;
        tables: Record<string, number>;
        migrations_applied: number | null;
      }
    | { db_available: false };
  log_tail: string[];
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

/** Owned working copy; pending edits are collected explicitly, never auto-merged. */
export interface ChildWorkspaceSummary {
  run_id: string;
  path: string;
  kind: "git_worktree" | "directory";
  state: "preparing" | "running" | "completed" | "failed" | "cancelled" | "needs_review" | "collected";
  files_written: string[];
  omitted: string[];
  collection_required: boolean;
}

export interface WorkspaceCheckResult {
  session_id: string; project_id: string | null; service_id: string;
  status: "ok" | "error" | "unknown";
  source: "session" | "project" | "default" | "unknown";
  path: string | null; reason: string | null;
}

export interface ChildWorkspaceReceipt {
  run_id: string; merged: Array<{ path: string; sha256: string }>; noop: string[];
  remaining?: string[];
  conflicts: Array<{ path: string; reason?: string }>; complete: boolean; pruned: boolean; already_collected?: boolean;
}

export interface ChildWorkspaceCollectionAttempt {
  id: string; run_id: string; version: 1;
  status: "in_progress" | "interrupted" | "partial" | "complete";
  operation?: string; backup?: string; path?: string; parent_sha256?: string;
  pending_path: string | null; report: ChildWorkspaceReceipt;
}

export interface ChildWorkspaceReview {
  run_id: string; session_id: string; project_id: string | null; service_id: string;
  root: string; revision: string; state: string; kind: string; omitted: string[]; receipt?: ChildWorkspaceReceipt;
  collection_attempts?: ChildWorkspaceCollectionAttempt[];
  team_collection?: { eligible: boolean; step_id: string | null; reason: string | null } | null;
  files: Array<{ path: string; baseline_sha256?: string | null; parent_sha256?: string | null; child_sha256?: string | null; owner_executable?: boolean; conflict: string | null; resolved?: boolean; merge_parent_text?: string; merge_child_text?: string; diff?: string; diff_truncated?: boolean; diff_unavailable?: boolean }>;
}

/** HTTP POST /preview/access; a temporary read-only preview path. */
export interface PreviewAccessResult {
  path: string;
  expires_in: number;
}

export interface SpawnSubagentResult {
  run_id: string;
  session_id: string;
  run_status: AgentRunStatus;
  text: string;
  agent: string;
  status: AgentRunStatus;
  agent_run_id: string;
  agent_id: string;
  workspace?: ChildWorkspaceSummary;
  budget_exhausted?: boolean;
  partial?: boolean;
  execution_target?: ExecutionTarget;
  execution_target_hash?: string;
  execution_installation_id?: string | null;
}

/* ─────────────────────── Git integration (§3-4 of v0.3.0 design) ─────────────────────── */

/** Return shape of `git.status`. */
export interface GitStatusResult {
  session_id?: string;
  workspace?: string;
  untracked_excluded?: number;
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
  session_id?: string;
  workspace?: string;
  scope: string;
  ref?: string;
  diff: string;
}
export interface WorktreePostMergeValidation {
  status: "passed" | "failed" | "unknown" | "skipped";
  reason: string;
  validation?: unknown;
}

export interface WorktreeCommittedReview {
  session_id: string; project_id: string; service_id: string; workspace: string; target: string;
  target_branch: string; target_head: string; source_head: string; merge_base: string;
  diff: string; commits: string[]; commits_truncated: boolean;
}

export interface RunWorkspaceDiffResult {
  run_id: string;
  session_id: string;
  project_id: string | null;
  workspace: string;
  workspace_identity: string;
  service_id: string;
  scope: "current_workspace";
  diff: string;
  staged_diff: string;
  untracked: string[];
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
  /** v1.3.0: scope the operation at this project's root. */
  project_id?: string;
}

export interface PatchHunkOperationResult {
  ok: true;
  operation: "apply_hunk" | "revert_hunk";
  scope: string;
  file_path: string;
  hunk_index: number;
}

export interface PatchFileOperationParams {
  scope?: "working" | "staged";
  file_path: string;
  /** v1.3.0: scope the operation at this project's root. */
  project_id?: string;
}

export interface PatchFileOperationResult {
  ok: true;
  operation: "apply_file" | "revert_file";
  scope: string;
  file_path: string;
}

export interface PatchApplyAllResult {
  ok: boolean;
  operation: "apply_all" | "revert_all";
  scope: string;
  applied: string[];
  failed: Array<{ file_path: string; error: string }>;
}

export interface PatchSaveSnapshotResult {
  ok: true;
  snapshot_ref: string | null;
  clean: boolean;
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
  | "cancelled"
  | "unknown";

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
  session_id?: string;
  run_status?: AgentRunStatus;
  request?: SpawnSubagentParams;
  admission_unknown?: boolean;
  cancel_requested?: boolean;
  partial?: boolean;
  execution_target?: ExecutionTarget;
  agent_id: string;
  agent_name: string;
  display_name?: string;
  parent_session_id?: string;
  parent_run_id?: string;
  recovery_service_id?: string;
  text_truncated?: boolean;
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
  AskUser: "agent.ask_user",
  AskUserClosed: "agent.ask_user_closed",
  PermissionRequest: "permission.request",
  PermissionResolved: "permission.resolved",
  PermissionClosed: "permission.closed",
  TaskProgress: "task.progress",
  ScheduleUpdated: "schedule.updated",
  SubAgentProgress: "agent.subagent_progress",
  NotificationNew: "notification.new",
  NotificationRead: "notification.read",
  TeamProgress: "agent.team_progress",
  RunCreated: "run.created",
  RunUpdated: "run.updated",
  RunStepStarted: "run.step.started",
  RunStepCompleted: "run.step.completed",
  RunCompleted: "run.completed",
  RunOutputCleared: "run.output_cleared",
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
export type OrchestrationMode =
  | "parallel"
  | "sequential"
  | "round-robin"
  | "vote"
  | "review";

/** An agent team template — matches `agent_teams` table row. */
export interface AgentTeam {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  agents: string[]; // array of agent names
  orchestration_mode: OrchestrationMode;
  /** Optional mode-specific configuration (e.g. `review_agent`). */
  orchestration_config?: Record<string, unknown>;
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
  session_id?: string;
  idempotency_key?: string;
  max_parallel?: number;
  status: "queued" | "started" | "agent_started" | "agent_completed" | "completed" | "failed" | "cancelled" | "interrupted";
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

// ---------------------------------------------------------------------------
// v0.11.0 — MCP server integration
// ---------------------------------------------------------------------------

export type McpTransport = "stdio" | "sse" | "streamable_http";

/** Persisted MCP server configuration — matches `mcp_servers` table row. */
export interface McpServer {
  id: string;
  name: string;
  transport: McpTransport;
  command: string[] | null;
  url: string | null;
  env: Record<string, string> | null;
  enabled: boolean;
  connected?: boolean;
  bearer_token: string | null;
  headers: Record<string, string> | null;
  oauth_client_id: string | null;
  oauth_client_secret: string | null;
  oauth_scopes: string[] | null;
  oauth_callback_port: number | null;
  tool_states: Record<string, boolean> | null;
  created_at: string;
  updated_at: string;
}

/** Exposed tool on an attached MCP server. */
export interface McpTool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
  outputSchema?: Record<string, unknown> | null;
}

/** Result of ``mcp.list_servers``. */
export interface ListMcpServersResult {
  servers: McpServer[];
}

/** Result of ``mcp.add_server`` / ``mcp.update_server``. */
export interface McpServerResult {
  server: McpServer;
}

/** Result of ``mcp.remove_server``. */
export interface RemoveMcpServerResult {
  ok: boolean;
  server_id: string;
}

/** Result of ``mcp.list_tools``. */
export interface ListMcpToolsResult {
  server_name: string;
  tools: McpTool[];
}

/** Result of ``mcp.invoke_tool``. */
export interface InvokeMcpToolResult {
  ok: boolean;
  server_name: string;
  tool_name: string;
  text: string | null;
  content: unknown[] | null;
  isError: boolean;
  /** Ordered MCP content blocks, including text and its annotations. */
  contentBlocks?: unknown[] | null;
  structuredContent?: unknown;
}
// ---------------------------------------------------------------------------
// v0.11.0 — Codebase RAG
// ---------------------------------------------------------------------------

/** Lifecycle status of the codebase indexer. */
export type CodebaseIndexStatus = "idle" | "indexing" | "done" | "error";

/** A single code search result returned by `codebase.search`. */
export interface CodebaseSearchResult {
  chunk_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  match_line?: number | null;
  snippet: string;
  language: string | null;
  rank: number;
  symbols: { name: string; kind: string; line: number; children: CodebaseSearchResult["symbols"] }[];
}

/** Result of `codebase.status` / `codebase.build_index`. */
export interface CodebaseStatusResult {
  status: CodebaseIndexStatus;
  processed: number;
  total: number;
  percent: number;
  message: string;
  error: string | null;
  stats: {
    total_chunks: number;
    total_files: number;
    latest_updated_at: string | null;
  };
}

/** Result of `codebase.search`. */
export interface CodebaseSearchResultShape {
  query: string;
  file_pattern: string | null;
  total: number;
  results: CodebaseSearchResult[];
}

export interface CodebaseReadCurrentParams {
  session_id: string;
  project_id: string;
  expected_service_id: string;
  path: string;
  start_line: number;
  end_line: number;
}

export interface CodebaseReadCurrentResult {
  path: string;
  start_line: number;
  end_line: number;
  content: string;
}

export interface McpResourcesParams {
  server_name: string; session_id: string; project_id: string; expected_service_id: string;
  action: "list" | "read"; connection_id?: string; uri?: string;
}
export interface McpResourcesResult {
  server_name: string; connection_id: string; session_id: string; project_id: string; service_id: string;
  data: { resources?: Array<{ uri: string; name: string; description: string; mimeType: string }>;
    omittedResources?: number; contents?: Array<{ uri: string; mimeType?: string; text?: string; blob?: string }>;
    omittedContents?: number };
}
export interface McpPromptsParams {
  server_name: string; session_id: string; project_id: string; expected_service_id: string;
  action: "list" | "get"; connection_id?: string; name?: string; arguments?: Record<string, string>;
}
export interface McpPromptsResult {
  server_name: string; connection_id: string; session_id: string; project_id: string; service_id: string;
  data: { prompts?: Array<{ name: string; description: string; arguments: Array<{ name: string; required: boolean; description: string }>; omittedArguments: number }>;
    omittedPrompts?: number; messages?: Array<{ role: string; content: { type: string; text?: string; resource?: { text?: string } } }>;
    omittedMessages?: number; prompt?: string; description?: string };
}

/** Result of `codebase.summarize`. */
export interface CodebaseSummarizeResult {
  path: string;
  kind: "file" | "directory";
  language: string | null;
  total_lines: number;
  symbols: { name: string; kind: string; line: number; children: CodebaseSummarizeResult["symbols"] }[];
  symbols_truncated?: boolean;
  snippet: string;
  file_count: number;
}

// ---------------------------------------------------------------------------
// v0.11.0 — Workspace checkpoints
// ---------------------------------------------------------------------------

/** A persisted workspace checkpoint — matches `session_checkpoints` table row. */
export interface Checkpoint {
  id: string;
  session_id: string;
  label: string;
  message: string;
  git_stash_ref: string | null;
  branch: string | null;
  tracked_files: string[];
  untracked_files: string[];
  has_untracked_snapshot: boolean;
  source_root?: string | null;
  created_at: string;
}

/** Result of `checkpoint.create`. */
export interface CheckpointCreateResult {
  checkpoint: Checkpoint;
  warnings?: string[];
  warnings_truncated?: boolean;
}

/** Result of `checkpoint.list`. */
export interface CheckpointListResult {
  checkpoints: Checkpoint[];
  count: number;
}

/** Result of `checkpoint.restore`. */
export interface CheckpointRestoreResult {
  result: {
    checkpoint_id: string;
    restored: boolean;
    applied_stash: boolean;
    restored_untracked: string[];
    skipped_existing: string[];
    warnings: string[];
  };
}

/** Result of `checkpoint.diff`. */
export interface CheckpointDiffResult {
  checkpoint_id: string;
  available: boolean;
  patch: string;
  files: string[];
  untracked_preview?: { path: string; status: "available" | "existing" | "missing_snapshot" | "unsafe_path"; sha256?: string }[];
}

/** Result of `checkpoint.delete`. */
export interface CheckpointDeleteResult {
  ok: true;
  removed_snapshot: boolean;
}

// ---------------------------------------------------------------------------
// Task ledger
// ---------------------------------------------------------------------------

/** A single persisted task row — matches the `tasks` table. */
export interface VerificationEvidence {
  /** Older records without a version/verdict remain unverified. */
  version?: 1;
  verdict?: "passed" | "failed" | "unknown" | "not_applicable";
  reason?: string;
  requires_review: boolean;
  tool_calls?: number;
  failed_tool_calls?: number;
  failures?: { tool: string; error: string }[];
  validation?: { command?: string; exit_code?: number | null; ok: boolean; timed_out?: boolean; error?: string; stdout?: string; stderr?: string; stdout_truncated?: boolean; stderr_truncated?: boolean };
}

export interface TaskRow {
  id: string;
  session_id: string;
  title: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "budget_paused" | "interrupted" | "requires_review";
  run_id?: string | null;
  execution_status?: string | null;
  verification?: VerificationEvidence;
  cancel_supported?: boolean;
  progress: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  /** v1.2.0 — the run's result text (e.g. the LLM reply of a scheduled prompt). */
  result: string | null;
}

/** Result of `task.list`. */
export interface TaskListResult {
  tasks: TaskRow[];
}

/** Result of `task.cancel`. */
export interface TaskCancelResult {
  requested?: boolean;
  ok: true;
  task: TaskRow;
  noop?: boolean;
}

// ---------------------------------------------------------------------------
// v0.11.0 — Long-term memory
// ---------------------------------------------------------------------------

/** Persisted memory category. */
export type MemoryCategory = "preference" | "decision" | "lesson" | "fact";

/** A single long-term memory row — matches the `memories` table. */
export interface MemoryEntry {
  id: string;
  project_id: string | null;
  session_id: string | null;
  content: string;
  category: MemoryCategory;
  confidence: number;
  source: string | null;
  created_at: string;
  updated_at: string;
}

/** Result of `memory.list` / `memory.search`. */
export interface ListMemoriesResult {
  memories: MemoryEntry[];
  total: number;
}

/** Result of `memory.add`. */
export interface MemoryAddResult {
  memory: MemoryEntry;
}

/** Result of `memory.delete`. */
export interface MemoryDeleteResult {
  ok: boolean;
  id: string;
}

/** Result of `memory.extract`. */
export interface MemoryExtractResult {
  facts: Array<{ content: string; category: MemoryCategory; confidence: number }>;
}

export type AttachmentDocumentCursor = { unit: number; offset: number };
export type AttachmentDocumentParams = { project_id: string; session_id: string; expected_service_id: string; attachment_id: string; expected_sha256: string } & AttachmentDocumentCursor;
export type AttachmentDocumentResult = { ok: true; project_id: string; session_id: string; service_id: string; attachment_id: string; sha256: string; kind: "pdf" | "docx" | "txt" | "csv"; unit: number; unit_total: number; location: string; start: number; end: number; text: string; truncated: boolean; next_cursor: AttachmentDocumentCursor | null; status: "text" | "empty"; ocr: "not_attempted" };

export type AttachmentOcrParams = { project_id: string; session_id: string; expected_service_id: string; attachment_id: string; expected_sha256: string; language: "eng" | "chi_sim" | "eng+chi_sim" };
export type AttachmentPdfOcrParams = AttachmentOcrParams & { page: number };
export type AttachmentPdfOcrResult = AttachmentOcrResult & { page: number };
export type AttachmentOcrResult = { ok: true; project_id: string; session_id: string; service_id: string; attachment_id: string; sha256: string; language: AttachmentOcrParams["language"]; quality: "unverified"; text: string; truncated: boolean };
