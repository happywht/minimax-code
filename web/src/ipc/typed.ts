/**
 * Typed high-level IPC API — the `TypedIPC` interface and the
 * `bindTypedIPC(client)` factory that maps friendly JS method names to
 * JSON-RPC wire methods on an `IPCClient`.
 *
 * Split out of `client.ts` during the ipc-module restructure; content
 * is preserved verbatim.
 */

import type {
  AgentInfo,
  AgentTeam,
  AuditStats,
  CreateProjectResult,
  CreateProviderResult,
  CreateSessionResult,
  DeleteProviderResult,
  GitDiffResult,
  GitLogResult,
  GitStatusResult,
  CrashDismissResult,
  CrashHistoryResult,
  CrashPreviousReportResult,
  ListAgentsResult,
  ListAuditResult,
  ListJobsResult,
  ListMessagesResult,
  UpdateMessageResult,
  DeleteMessageResult,
  UpdateProjectResult,
  ListModelsResult,
  ListNotificationsResult,
  ListPluginsResult,
  ListProjectsResult,
  ListProvidersResult,
  ListRulesResult,
  ListRunsResult,
  ListSessionsResult,
  ListSkillsResult,
  ListTeamsResult,
  ListWebhooksResult,
  ListWorkflowsResult,
  NotificationEntry,
  OrchestrationMode,
  PatchApplyAllResult,
  PatchFileOperationParams,
  PatchFileOperationResult,
  PatchHunkOperationParams,
  PatchHunkOperationResult,
  PatchPreviewResult,
  PatchSaveSnapshotResult,
  PermissionRule,
  PluginInfoResult,
  PluginReloadResult,
  PluginToggleResult,
  RunnerApprovalPolicy,
  RunnerInfo,
  RunnerListResult,
  RunnerPermissionMode,
  RunnerSandboxMode,
  RunnerStartResult,
  RunStepsResult,
  RuntimeRecoveryResult,
  ScheduledJob,
  SecretStatus,
  SendMessageResult,
  SessionExportResult,
  SessionStatsResult,
  SetModelResult,
  SetProviderApiKeyResult,
  SetReasoningEffortResult,
  SetRuleResult,
  SkillInfo,
  SpawnSubagentParams,
  SpawnSubagentResult,
  TelemetryMetrics,
  TelemetryRecentResult,
  TelemetryTraceResult,
  TerminalListResult,
  TerminalReadResult,
  TerminalStartResult,
  UpdateProviderResult,
  BatchArchiveSessionsResult,
  BatchUpdateSessionProjectResult,
  UpdateSessionProjectResult,
  UpdateSessionResult,
  WebhookConfig,
  WorkflowEntry,
  ListMcpServersResult,
  McpServerResult,
  RemoveMcpServerResult,
  ListMcpToolsResult,
  InvokeMcpToolResult,
  CodebaseStatusResult,
  CodebaseSearchResultShape,
  CodebaseSummarizeResult,
  ListMemoriesResult,
  MemoryAddResult,
  MemoryDeleteResult,
  MemoryExtractResult,
  MemoryCategory,
  CheckpointListResult,
  CheckpointCreateResult,
  CheckpointRestoreResult,
  CheckpointDiffResult,
  CheckpointDeleteResult,
  TaskListResult,
  TaskCancelResult,
} from "../types/ipc";
import type { IPCClient } from "./client";
/* ─────────────────────── Typed high-level API ─────────────────────── */

export interface TypedIPC {
  ping(): Promise<boolean>;

  // session
  listSessions(opts?: { archived?: boolean; limit?: number; offset?: number; search?: string }): Promise<ListSessionsResult>;
  createSession(opts?: {
    title?: string;
    reuse_empty_session_id?: string;
    model_id?: string;
    project_id?: string;
    workspace_mode?: "local" | "worktree";
    workspace_path?: string;
    worktree_branch?: string;
    base_branch?: string;
  }): Promise<CreateSessionResult>;
  createWorktreeSession(opts?: { title?: string; base_ref?: string }): Promise<CreateSessionResult>;
  listWorktreeSessions(): Promise<ListSessionsResult>;
  deleteWorktree(sessionId: string): Promise<UpdateSessionResult>;
  archiveSession(sessionId: string): Promise<{ ok: true }>;
  unarchiveSession(sessionId: string): Promise<{ ok: true }>;
  deleteSession(sessionId: string): Promise<{ ok: true }>;
  updateSession(sessionId: string, fields: { title?: string }): Promise<UpdateSessionResult>;
  updateSessionProject(sessionId: string, projectId: string): Promise<UpdateSessionProjectResult>;
  batchArchiveSessions(sessionIds: string[], archived: boolean): Promise<BatchArchiveSessionsResult>;
  batchUpdateSessionProject(sessionIds: string[], projectId: string): Promise<BatchUpdateSessionProjectResult>;
  sessionStats(): Promise<SessionStatsResult>;
  sessionExport(params: { session_id: string }): Promise<SessionExportResult>;
  listMessages(sessionId: string, opts?: { limit?: number; before?: string }): Promise<ListMessagesResult>;
  updateMessage(params: { message_id: string; content?: string; metadata?: Record<string, unknown> }): Promise<UpdateMessageResult>;
  deleteMessage(params: { message_id: string }): Promise<DeleteMessageResult>;

  // project
  listProjects(opts?: { archived?: boolean }): Promise<ListProjectsResult>;
  createProject(opts: { name: string; description?: string }): Promise<CreateProjectResult>;
  updateProject(projectId: string, fields: { name?: string; description?: string }): Promise<UpdateProjectResult>;
  deleteProject(projectId: string): Promise<{ ok: true; project_id: string }>;
  archiveProject(projectId: string): Promise<{ ok: true; project: import("../types/ipc").Project }>;
  unarchiveProject(projectId: string): Promise<{ ok: true; project: import("../types/ipc").Project }>;

  // mcp
  listMcpServers(): Promise<ListMcpServersResult>;
  addMcpServer(opts: {
    id: string;
    name: string;
    transport?: "stdio" | "sse";
    command?: string[];
    url?: string;
    env?: Record<string, string>;
    enabled?: boolean;
    bearer_token?: string;
    headers?: Record<string, string>;
    oauth_client_id?: string;
    oauth_client_secret?: string;
    oauth_scopes?: string[];
    oauth_callback_port?: number;
    tool_states?: Record<string, boolean>;
  }): Promise<McpServerResult>;
  updateMcpServer(serverId: string, opts: {
    name?: string;
    transport?: "stdio" | "sse";
    command?: string[];
    url?: string;
    env?: Record<string, string>;
    enabled?: boolean;
    bearer_token?: string;
    headers?: Record<string, string>;
    oauth_client_id?: string;
    oauth_client_secret?: string;
    oauth_scopes?: string[];
    oauth_callback_port?: number;
    tool_states?: Record<string, boolean>;
  }): Promise<McpServerResult>;
  removeMcpServer(serverId: string): Promise<RemoveMcpServerResult>;
  listMcpTools(serverName: string): Promise<ListMcpToolsResult>;
  invokeMcpTool(serverName: string, toolName: string, args?: Record<string, unknown>): Promise<InvokeMcpToolResult>;

  // codebase
  getCodebaseStatus(): Promise<CodebaseStatusResult>;
  buildCodebaseIndex(opts?: { force?: boolean }): Promise<CodebaseStatusResult>;
  searchCodebase(query: string, opts?: { file_pattern?: string; limit?: number; offset?: number }): Promise<CodebaseSearchResultShape>;
  summarizeCodebasePath(path: string): Promise<CodebaseSummarizeResult>;

  listRuns(opts?: { session_id?: string; status?: string; limit?: number; offset?: number }): Promise<ListRunsResult>;
  getRunSteps(runId: string): Promise<RunStepsResult>;

  // agent
  sendMessage(opts: { session_id: string | null; content: string | import("../types/ipc").ContentPart[]; attachments?: unknown }): Promise<SendMessageResult>;
  cancelAgent(sessionId: string): Promise<{ ok: true }>;

  // agent CRUD
  getAgent(name: string): Promise<{ agent: AgentInfo }>;
  createAgent(opts: {
    name: string;
    system_prompt: string;
    tool_allowlist?: string[];
    model?: string;
    description?: string;
    icon?: string;
    color?: string;
    category?: string;
    tags?: string[];
    skills?: string[];
    max_iterations?: number;
    temperature?: number;
  }): Promise<{ agent: AgentInfo }>;
  updateAgent(opts: {
    name: string;
    system_prompt?: string;
    tool_allowlist?: string[];
    model?: string;
    enabled?: boolean;
    description?: string;
    icon?: string;
    color?: string;
    category?: string;
    tags?: string[];
    skills?: string[];
    max_iterations?: number;
    temperature?: number;
  }): Promise<{ agent: AgentInfo }>;
  deleteAgent(name: string): Promise<{ ok: true }>;

  // model
  listModels(): Promise<ListModelsResult>;
  setCurrentModel(modelId: string): Promise<SetModelResult>;
  setReasoningEffort(effort: string | null): Promise<SetReasoningEffortResult>;

  // skill
  listSkills(): Promise<ListSkillsResult>;
  installSkill(content: string, replace?: boolean): Promise<{ skill: SkillInfo }>;
  uninstallSkill(skillId: string): Promise<{ ok: true; skill_id: string }>;
  enableSkill(skillId: string): Promise<{ ok: true }>;
  disableSkill(skillId: string): Promise<{ ok: true }>;
  invokeSkill(skillId: string, args: unknown): Promise<{ ok: true; output: unknown }>;

  // scheduler — wire = ``schedule.*`` (per handlers_scheduled.py).
  // The JS API is friendlier (cron / prompt) than the wire (cron_expr / payload)
  // and the binding translates at the boundary.
  listJobs(): Promise<ListJobsResult>;
  createJob(opts: { name: string; cron: string; prompt: string }): Promise<{ job: ScheduledJob }>;
  deleteJob(jobId: string): Promise<{ ok: true }>;
  enableJob(jobId: string): Promise<{ job: ScheduledJob }>;
  disableJob(jobId: string): Promise<{ job: ScheduledJob }>;
  runNowJob(jobId: string): Promise<{ ok: true; job_id: string; triggered_at: number | null }>;

  // agent (multi-agent)
  listAgents(): Promise<ListAgentsResult>;
  spawnSubagent(opts: SpawnSubagentParams): Promise<SpawnSubagentResult>;
  cancelSubagent(runId: string): Promise<{ ok: boolean; cancelled: boolean }>;

  // mobile
  startPairing(opts?: { suggested_name?: string }): Promise<{ token: string; expires_at: number; qr_payload: string }>;
  listDevices(): Promise<{ devices: { id: string; name: string; paired_at: number; online?: boolean }[] }>;
  unpairDevice(deviceId: string): Promise<{ ok: true; device_id: string }>;
  pushNotification(opts: {
    device_id?: string;
    notification: { type?: string; title: string; body?: string; priority?: number };
  }): Promise<{ ok: boolean; delivered?: boolean; broadcast?: boolean; total_devices?: number }>;
  deviceStatus(): Promise<{ devices: { id: string; name: string; paired_at: number; online: boolean }[] }>;
  // permission
  listRules(): Promise<ListRulesResult>;
  setRule(rule: Omit<PermissionRule, "id" | "created_at"> & { id?: string }): Promise<SetRuleResult>;
  deleteRule(ruleId: string): Promise<{ ok: true }>;
  resolvePermission(opts: {
    request_id: string;
    decision: "allow" | "deny";
  }): Promise<{ ok: boolean; request_id: string; decision?: string; reason?: string }>;

  // secrets (API key) — drive the Settings page's API Key tab.
  getSecretStatus(): Promise<SecretStatus>;
  setSecret(value: string): Promise<SecretStatus>;
  clearSecret(): Promise<SecretStatus>;

  // provider — drive the Settings page's Providers tab.
  listProviders(): Promise<ListProvidersResult>;
  createProvider(opts: {
    name: string;
    protocol: "anthropic" | "openai";
    base_url: string;
    api_key?: string;
    models?: { id: string; name: string; context_window: number; supports_tools: boolean; is_default?: boolean }[];
    enabled?: boolean;
  }): Promise<CreateProviderResult>;
  updateProvider(opts: {
    provider_id: string;
    name?: string;
    protocol?: "anthropic" | "openai";
    base_url?: string;
    api_key?: string;
    models?: { id: string; name: string; context_window: number; supports_tools: boolean; is_default?: boolean }[];
    enabled?: boolean;
  }): Promise<UpdateProviderResult>;
  deleteProvider(providerId: string): Promise<DeleteProviderResult>;
  setProviderApiKey(providerId: string, apiKey: string): Promise<SetProviderApiKeyResult>;
  clearProviderApiKey(providerId: string): Promise<SetProviderApiKeyResult>;

  // git (v0.3.0) — drives the top-bar GitStatusBar widget and the
  // code-review flow. ``gitStatus`` is the cheap call (the widget
  // polls it on a short interval); ``gitDiff`` and ``gitLog`` are
  // on-demand.
  gitStatus(): Promise<GitStatusResult>;
  gitDiff(opts: { scope?: "staged" | "branch" | "working"; ref?: string }): Promise<GitDiffResult>;
  gitLog(opts?: { n?: number }): Promise<GitLogResult>;
  // crash recovery (R231) — read persisted crash-report files written at boot.
  crashPreviousReport(): Promise<CrashPreviousReportResult>;
  crashHistory(): Promise<CrashHistoryResult>;
  crashDismiss(): Promise<CrashDismissResult>;
  patchPreview(opts?: { scope?: "staged" | "branch" | "working"; ref?: string }): Promise<PatchPreviewResult>;
  patchApplyHunk(opts: PatchHunkOperationParams): Promise<PatchHunkOperationResult>;
  patchRevertHunk(opts: PatchHunkOperationParams): Promise<PatchHunkOperationResult>;
  patchApplyFile(opts: PatchFileOperationParams): Promise<PatchFileOperationResult>;
  patchRevertFile(opts: PatchFileOperationParams): Promise<PatchFileOperationResult>;
  patchApplyAll(opts?: { scope?: "staged" | "working" }): Promise<PatchApplyAllResult>;
  patchRevertAll(opts?: { scope?: "staged" | "working" }): Promise<PatchApplyAllResult>;
  patchSaveSnapshot(): Promise<PatchSaveSnapshotResult>;
  startTerminal(opts: {
    command: string;
    cwd?: string;
    timeout_s?: number;
    session_id?: string | null;
  }): Promise<TerminalStartResult>;
  readTerminal(opts: { session_id: string; after_seq?: number }): Promise<TerminalReadResult>;
  stopTerminal(sessionId: string): Promise<TerminalStartResult>;
  listTerminals(): Promise<TerminalListResult>;
  listRunners(): Promise<RunnerListResult>;
  startRunner(opts: {
    runner_id: RunnerInfo["id"];
    command: string;
    cwd?: string;
    timeout_s?: number;
    session_id?: string | null;
    sandbox_mode?: RunnerSandboxMode;
    approval_policy?: RunnerApprovalPolicy;
    permission_mode?: RunnerPermissionMode;
  }): Promise<RunnerStartResult>;

  // audit — drive the Settings page's Audit tab.
  listAudit(opts?: { limit?: number; offset?: number; tool_name?: string; session_id?: string }): Promise<ListAuditResult>;
  auditStats(): Promise<AuditStats>;
  purgeAudit(beforeIso: string): Promise<{ deleted: number }>;

  // telemetry — in-memory observability bus (R11).
  telemetryRecent(opts?: { limit?: number; event_type?: string; session_id?: string }): Promise<TelemetryRecentResult>;
  telemetryMetrics(opts?: { session_id?: string }): Promise<TelemetryMetrics>;
  telemetryClear(): Promise<{ ok: boolean; cleared: number; enabled: boolean }>;
  telemetryTrace(trace_id: string): Promise<TelemetryTraceResult>;

  // runtime — boot-time crash-recovery diagnostics (R12).
  runtimeRecoveryStatus(): Promise<RuntimeRecoveryResult>;

  // webhook — drive the Settings page's Webhooks tab.
  listWebhooks(opts?: { limit?: number; offset?: number; source?: string }): Promise<ListWebhooksResult>;
  createWebhook(opts: { name: string; source?: string; action_type?: string; action_config?: Record<string, unknown> }): Promise<WebhookConfig>;
  updateWebhook(id: string, fields: Partial<Pick<WebhookConfig, "name" | "source" | "enabled" | "action_type" | "action_config">>): Promise<WebhookConfig>;
  deleteWebhook(id: string): Promise<{ deleted: boolean }>;
  regenerateWebhookSecret(id: string): Promise<WebhookConfig>;

  // notification — drive the NotificationBell / NotificationCenter UI.
  listNotifications(opts?: { limit?: number; offset?: number; type?: string; source?: string; unread_only?: boolean }): Promise<ListNotificationsResult>;
  markNotificationRead(id: string): Promise<NotificationEntry>;
  markAllNotificationsRead(): Promise<{ marked: number }>;
  deleteNotification(id: string): Promise<{ deleted: boolean }>;
  purgeNotifications(beforeIso: string, readOnly?: boolean): Promise<{ purged: number }>;

  // workflow — drive the Settings page's Workflows tab.
  listWorkflows(opts?: { limit?: number; offset?: number; trigger_type?: string; enabled_only?: boolean }): Promise<ListWorkflowsResult>;
  createWorkflow(opts: { name: string; trigger_type: string; description?: string; trigger_config?: Record<string, unknown>; steps?: unknown[]; enabled?: boolean }): Promise<WorkflowEntry>;
  updateWorkflow(id: string, fields: { name?: string; description?: string; trigger_config?: Record<string, unknown>; steps?: unknown[] }): Promise<WorkflowEntry>;
  deleteWorkflow(id: string): Promise<{ deleted: boolean }>;
  enableWorkflow(id: string): Promise<WorkflowEntry>;
  disableWorkflow(id: string): Promise<WorkflowEntry>;
  triggerWorkflow(id: string, context?: Record<string, unknown>): Promise<{ ok: boolean; steps_completed: number; steps_failed: number }>;

  // team (v0.8.0) — drive the Settings page's Teams tab.
  listTeams(): Promise<ListTeamsResult>;
  getTeam(name: string): Promise<{ team: AgentTeam }>;
  createTeam(opts: {
    name: string;
    description?: string;
    icon?: string;
    color?: string;
    agents?: string[];
    orchestration_mode?: OrchestrationMode;
  }): Promise<{ team: AgentTeam }>;
  updateTeam(name: string, fields: {
    description?: string;
    icon?: string;
    color?: string;
    agents?: string[];
    orchestration_mode?: OrchestrationMode;
  }): Promise<{ team: AgentTeam }>;
  deleteTeam(name: string): Promise<{ ok: true; name: string }>;
  enableTeam(name: string): Promise<{ team: AgentTeam }>;
  disableTeam(name: string): Promise<{ team: AgentTeam }>;
  spawnTeam(opts: {
    team_name: string;
    request: string;
    session_id?: string;
    parent_session_id?: string;
  }): Promise<{
    team_name: string;
    orchestration_mode: string;
    merged_text: string;
    agents_run: Array<{
      agent_name: string;
      success: boolean;
      text: string;
      error: string;
      iterations: number;
      stub: boolean;
    }>;
    conflicts: Array<{
      file_path: string;
      agents: string[];
      conflict_type: string;
    }>;
    task_id: string | null;
    success: boolean;
  }>;

  // plugins (platform pillar #3) — drive the Settings page's Plugins tab.
  listPlugins(params?: { include_failed?: boolean; enabled_only?: boolean }): Promise<ListPluginsResult>;
  getPlugin(name: string): Promise<PluginInfoResult>;
  enablePlugin(name: string): Promise<PluginToggleResult>;
  disablePlugin(name: string): Promise<PluginToggleResult>;
  reloadPlugins(): Promise<PluginReloadResult>;

  // checkpoint (v0.11.0) — drive the RightPanel checkpoint panel.
  listCheckpoints(sessionId: string, opts?: { limit?: number; offset?: number }): Promise<CheckpointListResult>;
  createCheckpoint(opts: { session_id: string; label?: string; message?: string; cwd?: string }): Promise<CheckpointCreateResult>;
  restoreCheckpoint(checkpointId: string, opts?: { cwd?: string }): Promise<CheckpointRestoreResult>;
  diffCheckpoint(checkpointId: string, opts?: { cwd?: string }): Promise<CheckpointDiffResult>;
  deleteCheckpoint(checkpointId: string): Promise<CheckpointDeleteResult>;

  // task (v0.11.0) — drive the RightPanel progress ledger.
  listTasks(opts?: { session_id?: string; status?: string; limit?: number }): Promise<TaskListResult>;
  cancelTask(taskId: string): Promise<TaskCancelResult>;

  // memory (v0.11.0) — drive the Settings page's Memory tab.
  listMemories(opts?: {
    project_id?: string;
    session_id?: string;
    category?: MemoryCategory;
    limit?: number;
    offset?: number;
  }): Promise<ListMemoriesResult>;
  searchMemories(
    query: string,
    opts?: { project_id?: string; session_id?: string; category?: MemoryCategory; limit?: number },
  ): Promise<ListMemoriesResult>;
  addMemory(opts: {
    content: string;
    category?: MemoryCategory;
    confidence?: number;
    project_id?: string;
    session_id?: string;
    source?: string;
  }): Promise<MemoryAddResult>;
  deleteMemory(id: string): Promise<MemoryDeleteResult>;
  extractMemoryFacts(text: string): Promise<MemoryExtractResult>;
}

interface WireScheduledJob {
  id: string;
  name: string;
  cron?: string;
  cron_expr?: string;
  prompt?: string;
  payload?: { prompt?: unknown } | null;
  enabled: boolean;
  last_run_at: number | null;
  next_run_at: number | null;
}

function normalizeScheduledJob(job: WireScheduledJob): ScheduledJob {
  const payloadPrompt = job.payload?.prompt;
  return {
    id: job.id,
    name: job.name,
    cron: job.cron ?? job.cron_expr ?? "",
    prompt: job.prompt ?? (typeof payloadPrompt === "string" ? payloadPrompt : ""),
    enabled: job.enabled,
    last_run_at: job.last_run_at,
    next_run_at: job.next_run_at,
  };
}

export function bindTypedIPC(client: IPCClient): TypedIPC {
  return {
    ping: () => client.ping(),

    listSessions: (opts) =>
      client.request<ListSessionsResult>("session.list", opts ?? {}),
    createSession: (opts) =>
      client.request<CreateSessionResult>("session.create", opts ?? {}),
    createWorktreeSession: (opts) =>
      client.request<CreateSessionResult>("workspace.create_worktree_session", opts ?? {}),
    listWorktreeSessions: () =>
      client.request<ListSessionsResult>("workspace.list_worktrees", {}),
    deleteWorktree: (sid) =>
      client.request<UpdateSessionResult>("workspace.delete_worktree", { session_id: sid }),
    archiveSession: (sid) =>
      client.request<{ ok: true }>("session.archive", { session_id: sid }),
    unarchiveSession: (sid) =>
      client.request<{ ok: true }>("session.unarchive", { session_id: sid }),
    deleteSession: (sid) =>
      client.request<{ ok: true }>("session.delete", { session_id: sid }),
    updateSession: (sid, fields) =>
      client.request<UpdateSessionResult>("session.update", {
        session_id: sid,
        ...fields,
      }),
    updateSessionProject: (sid, projectId) =>
      client.request<UpdateSessionProjectResult>("session.updateProject", {
        session_id: sid,
        project_id: projectId,
      }),
    batchArchiveSessions: (sessionIds, archived) =>
      client.request<BatchArchiveSessionsResult>("session.batchArchive", {
        session_ids: sessionIds,
        archived,
      }),
    batchUpdateSessionProject: (sessionIds, projectId) =>
      client.request<BatchUpdateSessionProjectResult>("session.batchUpdateProject", {
        session_ids: sessionIds,
        project_id: projectId,
      }),
    sessionStats: () => client.request<SessionStatsResult>("session.stats", {}),
    sessionExport: (params) =>
      client.request<SessionExportResult>("session.export", params),
    listMessages: (sid, opts) =>
      client.request<ListMessagesResult>("message.list", {
        session_id: sid,
        ...(opts ?? {}),
      }),
    updateMessage: (params) =>
      client.request<UpdateMessageResult>("message.update", params),
    deleteMessage: (params) =>
      client.request<DeleteMessageResult>("message.delete", params),

    listProjects: (opts) =>
      client.request<ListProjectsResult>("project.list", opts ?? {}),
    createProject: (opts) =>
      client.request<CreateProjectResult>("project.create", opts),
    updateProject: (pid, fields) =>
      client.request<UpdateProjectResult>("project.update", { project_id: pid, ...fields }),
    deleteProject: (pid) =>
      client.request<{ ok: true; project_id: string }>("project.delete", { project_id: pid }),
    archiveProject: (pid) =>
      client.request<{ ok: true; project: import("../types/ipc").Project }>("project.archive", { project_id: pid }),
    unarchiveProject: (pid) =>
      client.request<{ ok: true; project: import("../types/ipc").Project }>("project.unarchive", { project_id: pid }),

    listMcpServers: () => client.request<ListMcpServersResult>("mcp.list_servers", {}),
    addMcpServer: (opts) => client.request<McpServerResult>("mcp.add_server", opts),
    updateMcpServer: (serverId, opts) =>
      client.request<McpServerResult>("mcp.update_server", { server_id: serverId, ...opts }),
    removeMcpServer: (serverId) =>
      client.request<RemoveMcpServerResult>("mcp.remove_server", { server_id: serverId }),
    listMcpTools: (serverName) =>
      client.request<ListMcpToolsResult>("mcp.list_tools", { server_name: serverName }),
    invokeMcpTool: (serverName, toolName, args) =>
      client.request<InvokeMcpToolResult>("mcp.invoke_tool", {
        server_name: serverName,
        tool_name: toolName,
        arguments: args ?? {},
      }),

    getCodebaseStatus: () => client.request<CodebaseStatusResult>("codebase.status", {}),
    buildCodebaseIndex: (opts) =>
      client.request<CodebaseStatusResult>("codebase.build_index", opts ?? {}),
    searchCodebase: (query, opts) =>
      client.request<CodebaseSearchResultShape>("codebase.search", {
        query,
        file_pattern: opts?.file_pattern,
        limit: opts?.limit,
        offset: opts?.offset,
      }),
    summarizeCodebasePath: (path) =>
      client.request<CodebaseSummarizeResult>("codebase.summarize", { path }),

    listRuns: (opts) =>
      client.request<ListRunsResult>("run.list", opts ?? {}),
    getRunSteps: (runId) =>
      client.request<RunStepsResult>("run.steps", { run_id: runId }),

    sendMessage: (opts) =>
      client.request<SendMessageResult>("agent.send_message", opts),
    cancelAgent: (sid) =>
      client.request<{ ok: true }>("agent.cancel", { session_id: sid }),

    // Agent CRUD
    getAgent: (name) =>
      client.request<{ agent: AgentInfo }>("agent.get", { name }),
    createAgent: (opts) =>
      client.request<{ agent: AgentInfo }>("agent.create", opts),
    updateAgent: (opts) =>
      client.request<{ agent: AgentInfo }>("agent.update", opts),
    deleteAgent: (name) =>
      client.request<{ ok: true }>("agent.delete", { name }),

    listModels: () => client.request<ListModelsResult>("model.list", {}),
    setCurrentModel: (modelId) =>
      client.request<SetModelResult>("model.set_current", { model_id: modelId }),
    setReasoningEffort: (effort) =>
      client.request<SetReasoningEffortResult>("model.set_reasoning_effort", {
        reasoning_effort: effort,
      }),

    listSkills: () => client.request<ListSkillsResult>("skill.list", {}),
    installSkill: (content, replace = false) =>
      client.request<{ skill: SkillInfo }>("skill.install", { content, replace }),
    uninstallSkill: (skillId) =>
      client.request<{ ok: true; skill_id: string }>("skill.uninstall", { skill_id: skillId }),
    enableSkill: (sid) =>
      client.request<{ ok: true }>("skill.enable", { skill_id: sid }),
    disableSkill: (sid) =>
      client.request<{ ok: true }>("skill.disable", { skill_id: sid }),
    invokeSkill: (sid, args) =>
      client.request<{ ok: true; output: unknown }>("skill.invoke", {
        skill_id: sid,
        request: args,
      }),

    listJobs: async () => {
      const result = await client.request<{ jobs: WireScheduledJob[] }>("schedule.list", {});
      return { jobs: result.jobs.map(normalizeScheduledJob) } satisfies ListJobsResult;
    },
    createJob: async (opts) => {
      const result = await client.request<{ job: WireScheduledJob }>("schedule.create", {
        name: opts.name,
        // Wire names differ from the JS API for historical reasons.
        cron_expr: opts.cron,
        payload: { prompt: opts.prompt },
      });
      return { job: normalizeScheduledJob(result.job) };
    },
    deleteJob: (jid) =>
      client.request<{ ok: true }>("schedule.delete", { job_id: jid }),
    enableJob: async (jid) => {
      const result = await client.request<{ job: WireScheduledJob }>("schedule.enable", { job_id: jid });
      return { job: normalizeScheduledJob(result.job) };
    },
    disableJob: async (jid) => {
      const result = await client.request<{ job: WireScheduledJob }>("schedule.disable", { job_id: jid });
      return { job: normalizeScheduledJob(result.job) };
    },
    runNowJob: (jid) =>
      client.request<{ ok: true; job_id: string; triggered_at: number | null }>("schedule.run_now", { job_id: jid }),

    listAgents: () => client.request<ListAgentsResult>("agent.list", {}),
    // Map frontend keys → backend keys. New callers send agent_name
    // because the backend registry is keyed by agents.name. agent_id
    // remains as a legacy fallback for older UI/store paths.
    spawnSubagent: (opts) =>
      client.request<SpawnSubagentResult>("agent.spawn_subagent", {
        name: opts.agent_name ?? opts.agent_id,
        agent_id: opts.agent_id,
        request: opts.prompt,
        parent_session_id: opts.parent_session_id,
        context_message_id: opts.context_message_id,
        display_name: opts.display_name,
        run_id: opts.run_id,
      }),
    cancelSubagent: (runId) =>
      client.request<{ ok: boolean; cancelled: boolean }>("agent.cancel_subagent", { run_id: runId }),

    startPairing: (opts) =>
      client.request<{ token: string; expires_at: number; qr_payload: string }>(
        "mobile.pair_start",
        opts ?? {},
      ),
    listDevices: () =>
      client.request<{
        devices: { id: string; name: string; paired_at: number; online?: boolean }[];
      }>("mobile.list", {}),
    unpairDevice: (did) =>
      client.request<{ ok: true; device_id: string }>("mobile.unpair", {
        device_id: did,
      }),
    pushNotification: (opts) =>
      client.request<{
        ok: boolean; delivered?: boolean; broadcast?: boolean;
        total_devices?: number; device_id?: string;
      }>("mobile.push_notification", opts),
    deviceStatus: () =>
      client.request<{
        devices: { id: string; name: string; paired_at: number; online: boolean }[];
      }>("mobile.device_status", {}),
    listRules: () => client.request<ListRulesResult>("permission.list", {}),
    setRule: (rule) =>
      client.request<SetRuleResult>("permission.set", rule),
    deleteRule: (toolPattern) =>
      client.request<{ ok: true }>("permission.delete", { tool_pattern: toolPattern }),
    resolvePermission: (opts) =>
      client.request<{
        ok: boolean;
        request_id: string;
        decision?: string;
        reason?: string;
      }>("permission.resolve", opts),

    getSecretStatus: () => client.request<SecretStatus>("secrets.status", {}),
    setSecret: (value) => client.request<SecretStatus>("secrets.set", { value }),
    clearSecret: () => client.request<SecretStatus>("secrets.clear", {}),

    listProviders: () => client.request<ListProvidersResult>("provider.list", {}),
    createProvider: (opts) =>
      client.request<CreateProviderResult>("provider.create", opts),
    updateProvider: (opts) =>
      client.request<UpdateProviderResult>("provider.update", opts),
    deleteProvider: (providerId) =>
      client.request<DeleteProviderResult>("provider.delete", { provider_id: providerId }),
    setProviderApiKey: (providerId, apiKey) =>
      client.request<SetProviderApiKeyResult>("provider.set_api_key", { provider_id: providerId, api_key: apiKey }),
    clearProviderApiKey: (providerId) =>
      client.request<SetProviderApiKeyResult>("provider.clear_api_key", { provider_id: providerId }),

    gitStatus: () => client.request<GitStatusResult>("git.status", {}),
    gitDiff: (opts) => client.request<GitDiffResult>("git.diff", opts ?? {}),
    gitLog: (opts) => client.request<GitLogResult>("git.log", opts ?? {}),
    // ── Crash recovery (R231) — read persisted crash-report files written at boot ──
    crashPreviousReport: () =>
      client.request<CrashPreviousReportResult>("crash.previous_report", {}),
    crashHistory: () => client.request<CrashHistoryResult>("crash.history", {}),
    crashDismiss: () => client.request<CrashDismissResult>("crash.dismiss", {}),
    patchPreview: (opts) => client.request<PatchPreviewResult>("patch.preview", opts ?? {}),
    patchApplyHunk: (opts) => client.request<PatchHunkOperationResult>("patch.apply_hunk", opts),
    patchRevertHunk: (opts) => client.request<PatchHunkOperationResult>("patch.revert_hunk", opts),
    patchApplyFile: (opts) => client.request<PatchFileOperationResult>("patch.apply_file", opts),
    patchRevertFile: (opts) => client.request<PatchFileOperationResult>("patch.revert_file", opts),
    patchApplyAll: (opts) => client.request<PatchApplyAllResult>("patch.apply_all", opts ?? {}),
    patchRevertAll: (opts) => client.request<PatchApplyAllResult>("patch.revert_all", opts ?? {}),
    patchSaveSnapshot: () => client.request<PatchSaveSnapshotResult>("patch.save_snapshot", {}),
    startTerminal: (opts) => client.request<TerminalStartResult>("terminal.start", opts),
    readTerminal: (opts) => client.request<TerminalReadResult>("terminal.read", opts),
    stopTerminal: (sessionId) => client.request<TerminalStartResult>("terminal.stop", { session_id: sessionId }),
    listTerminals: () => client.request<TerminalListResult>("terminal.list", {}),
    listRunners: () => client.request<RunnerListResult>("runner.list", {}),
    startRunner: (opts) => client.request<RunnerStartResult>("runner.start", opts),

    listAudit: (opts) => client.request<ListAuditResult>("audit.list", opts ?? {}),
    auditStats: () => client.request<AuditStats>("audit.stats", {}),
    purgeAudit: (beforeIso) => client.request<{ deleted: number }>("audit.purge", { before_iso: beforeIso }),
    telemetryRecent: (opts) =>
      client.request<TelemetryRecentResult>("telemetry.recent", opts ?? {}),
    telemetryMetrics: (opts) => client.request<TelemetryMetrics>("telemetry.metrics", opts ?? {}),
    telemetryClear: () =>
      client.request<{ ok: boolean; cleared: number; enabled: boolean }>("telemetry.clear", {}),
    telemetryTrace: (trace_id) =>
      client.request<TelemetryTraceResult>("telemetry.trace", { trace_id }),
    runtimeRecoveryStatus: () =>
      client.request<RuntimeRecoveryResult>("runtime.recovery_status", {}),

    listWebhooks: (opts) => client.request<ListWebhooksResult>("webhook.list", opts ?? {}),
    createWebhook: (opts) => client.request<WebhookConfig>("webhook.create", opts),
    updateWebhook: (id, fields) => client.request<WebhookConfig>("webhook.update", { id, ...fields }),
    deleteWebhook: (id) => client.request<{ deleted: boolean }>("webhook.delete", { id }),
    regenerateWebhookSecret: (id) => client.request<WebhookConfig>("webhook.regenerate_secret", { id }),

    listNotifications: (opts) =>
      client.request<ListNotificationsResult>("notification.list", opts ?? {}),
    markNotificationRead: (id) =>
      client.request<NotificationEntry>("notification.mark_read", { id }),
    markAllNotificationsRead: () =>
      client.request<{ marked: number }>("notification.mark_all_read", {}),
    deleteNotification: (id) =>
      client.request<{ deleted: boolean }>("notification.delete", { id }),
    purgeNotifications: (beforeIso, readOnly) =>
      client.request<{ purged: number }>("notification.purge", { before_iso: beforeIso, read_only: readOnly ?? false }),

    listWorkflows: (opts) =>
      client.request<ListWorkflowsResult>("workflow.list", opts ?? {}),
    createWorkflow: (opts) =>
      client.request<WorkflowEntry>("workflow.create", opts),
    updateWorkflow: (id, fields) =>
      client.request<WorkflowEntry>("workflow.update", { id, ...fields }),
    deleteWorkflow: (id) =>
      client.request<{ deleted: boolean }>("workflow.delete", { id }),
    enableWorkflow: (id) =>
      client.request<WorkflowEntry>("workflow.enable", { id }),
    disableWorkflow: (id) =>
      client.request<WorkflowEntry>("workflow.disable", { id }),
    triggerWorkflow: (id, context) =>
      client.request<{ ok: boolean; steps_completed: number; steps_failed: number }>("workflow.trigger", { id, context }),

    // team (v0.8.0)
    listTeams: () => client.request<ListTeamsResult>("team.list", {}),
    getTeam: (name) => client.request<{ team: AgentTeam }>("team.get", { name }),
    createTeam: (opts) => client.request<{ team: AgentTeam }>("team.create", opts),
    updateTeam: (name, fields) => client.request<{ team: AgentTeam }>("team.update", { name, ...fields }),
    deleteTeam: (name) => client.request<{ ok: true; name: string }>("team.delete", { name }),
    enableTeam: (name) => client.request<{ team: AgentTeam }>("team.enable", { name }),
    disableTeam: (name) => client.request<{ team: AgentTeam }>("team.disable", { name }),
    spawnTeam: (opts) => client.request("team.spawn", opts),
    // plugins (platform pillar #3)
    listPlugins: (params) =>
      client.request<ListPluginsResult>("plugins.list", params ?? {}),
    getPlugin: (name) => client.request<PluginInfoResult>("plugins.info", { name }),
    enablePlugin: (name) => client.request<PluginToggleResult>("plugins.enable", { name }),
    disablePlugin: (name) => client.request<PluginToggleResult>("plugins.disable", { name }),
    reloadPlugins: () => client.request<PluginReloadResult>("plugins.reload", {}),

    // checkpoint (v0.11.0)
    listCheckpoints: (sessionId, opts) =>
      client.request<CheckpointListResult>("checkpoint.list", { session_id: sessionId, ...(opts ?? {}) }),
    createCheckpoint: (opts) =>
      client.request<CheckpointCreateResult>("checkpoint.create", opts),
    restoreCheckpoint: (checkpointId, opts) =>
      client.request<CheckpointRestoreResult>("checkpoint.restore", { checkpoint_id: checkpointId, ...(opts ?? {}) }),
    diffCheckpoint: (checkpointId, opts) =>
      client.request<CheckpointDiffResult>("checkpoint.diff", { checkpoint_id: checkpointId, ...(opts ?? {}) }),
    deleteCheckpoint: (checkpointId) =>
      client.request<CheckpointDeleteResult>("checkpoint.delete", { checkpoint_id: checkpointId }),

    // task (v0.11.0)
    listTasks: (opts) => client.request<TaskListResult>("task.list", opts ?? {}),
    cancelTask: (taskId) => client.request<TaskCancelResult>("task.cancel", { task_id: taskId }),

    // memory (v0.11.0)
    listMemories: (opts) => client.request<ListMemoriesResult>("memory.list", opts ?? {}),
    searchMemories: (query, opts) =>
      client.request<ListMemoriesResult>("memory.search", { query, ...(opts ?? {}) }),
    addMemory: (opts) => client.request<MemoryAddResult>("memory.add", opts),
    deleteMemory: (id) => client.request<MemoryDeleteResult>("memory.delete", { id }),
    extractMemoryFacts: (text) =>
      client.request<MemoryExtractResult>("memory.extract", { text }),
  };
}
