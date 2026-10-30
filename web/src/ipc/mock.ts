/**
 * Mock backend — in-process JSON-RPC handler that lets the UI shell
 * render in a plain browser (or under vitest) without the Python agent.
 * Real answers come from the Python agent over HTTP + WebSocket.
 *
 * Split out of `client.ts` during the ipc-module restructure; the
 * `mockHandle` switch and its helpers are preserved verbatim.
 */

import { StreamEvent } from "../types/ipc";
import { artifactMock } from "./artifactMock";
import { attachmentMock } from "./attachmentMock";
import { DEFAULT_SESSION_TITLE, DEFAULT_WORKTREE_TITLE } from "../lib/defaultTitles";
import type { BackendPermissionRule } from "./typed";
import type {
  AgentInfo,
  AgentTeam,
  AuditStats,
  CreateProviderResult,
  CrashDismissResult,
  CrashHistoryResult,
  CrashPreviousReportResult,
  DeleteProviderResult,
  GitDiffResult,
  GitLogResult,
  GitStatusResult,
  JsonRpcId,
  DataBackupResult,
  DataExportEnvelope,
  DataImportSummary,
  DiagnosticBundle,
  ListAuditResult,
  ListNotificationsResult,
  ListPluginsResult,
  ListProvidersResult,
  ListRunsResult,
  ListTeamsResult,
  ListWebhooksResult,
  ListWorkflowsResult,
  MessageChunkData,
  Message as ProtocolMessage,
  NotificationEntry,
  OrchestrationMode,
  PatchApplyAllResult,
  PatchFileOperationParams,
  PatchFileOperationResult,
  PatchHunkOperationParams,
  PatchHunkOperationResult,
  PatchPreviewResult,
  PatchSaveSnapshotResult,
  PluginInfo,
  PluginInfoResult,
  PluginReloadResult,
  PluginToggleResult,
  ProviderInfo,
  RunnerApprovalPolicy,
  RunnerInfo,
  RunnerListResult,
  RunnerPermissionMode,
  RunnerSandboxMode,
  RunnerStartResult,
  RuntimeRecoveryResult,
  ScheduledJob,
  Session,
  SessionExportResult,
  SessionStatsResult,
  UpdateMessageResult,
  DeleteMessageResult,
  SetProviderApiKeyResult,
  SkillInfo,
  TelemetryMetrics,
  TelemetryRecentResult,
  TerminalListResult,
  TerminalReadResult,
  TerminalStartResult,
  UpdateProviderResult,
  WebhookConfig,
  WorkflowEntry,
  McpTransport,
  McpServer,
  ListMcpServersResult,
  McpServerResult,
  RemoveMcpServerResult,
  ListMcpToolsResult,
  InvokeMcpToolResult,
  CodebaseStatusResult,
  CodebaseSearchResultShape,
  CodebaseSummarizeResult,
  MemoryEntry,
  MemoryCategory,
  ListMemoriesResult,
  MemoryAddResult,
  MemoryDeleteResult,
  MemoryExtractResult,
  Checkpoint,
  CheckpointListResult,
  CheckpointCreateResult,
  CheckpointRestoreResult,
  CheckpointDiffResult,
  CheckpointDeleteResult,
  TaskListResult,
  TaskRow,
} from "../types/ipc";
import type { IPCClient } from "./client";
import {
  makeMockTerminalSession,
  mockAgents,
  mockJobs,
  mockModels,
  mockPlugins,
  mockProjects,
  mockProviders,
  mockRunners,
  mockRuns,
  mockSecrets,
  mockSessions,
  mockSessionsWithMessages,
  mockSkills,
  mockTeams,
  mockTerminalChunks,
  mockTerminalSessions,
  mockCheckpoints,
} from "./mockData";
/**
 * The mock backend is intentionally minimal — it just lets the UI shell
 * render in a plain browser (or under vitest) without the Python agent.
 * Real answers come from the Python agent over HTTP + WebSocket.
 */
export function mockRequest<T>(
  method: string,
  params: unknown,
  client: IPCClient,
): Promise<T> {
  const id =
    (params as { __id?: JsonRpcId } | undefined)?.__id ??
    `mock-${Math.random().toString(36).slice(2)}`;
  // Propagate validation failures instead of leaving the outer promise pending.
  return Promise.resolve().then(() => mockHandle(method, params, client, id) as T);
}

export function mockNotify(method: string, params: unknown, client: IPCClient): void {
  mockHandle(method, params, client, null);
}
// R63: mock-side echo of the persisted reasoning-effort override. Mirrors the
// R61 backend write-side read-back — the frontend badge switcher consumes it
// from `model.list` so it stays in sync with the store without a second round-trip.
let mockReasoningEffort: string | null = null;
let mockResearchUrl: string | null = null;
const mockMcpServers = new Map<string, McpServer>();
const mockMemories = new Map<string, MemoryEntry>();
// R18 — user-created permission rules in *wire* shape (the factory
// exec_* → ask default is synthesised on read, like the real store).
const mockPermissionRules: BackendPermissionRule[] = [];
// R21–R23 — in-memory echo of the data-portability envelope. data.import
// validates + stores it; data.export replays it (or a default envelope
// before any import) so the Settings Data tab round-trips in browser-only
// mode. data.backup has no filesystem here — it reports a fake snapshot.
let mockDataEnvelope: DataExportEnvelope | null = null;
const mockRunReceipts = new Map<string, import("../types/ipc").SendMessageResult>();
const mockRunRequests = new Map<string, import("../types/ipc").SendMessageParams>();
const mockAgentKeys = new Map<string, { signature: string; runId: string }>();
const mockSkillKeys = new Map<string, { signature: string; runId: string }>();
const mockScheduleCreateKeys = new Map<string, { signature: string; jobId: string }>();
const mockRequestKeys = new Map<string, { signature: string; runId: string }>();
const mockTeamRequests = new Map<string, { signature: string; result: Record<string, unknown> }>();
const mockRunEvents = new Map<string, import("../types/ipc").RunEvent[]>();
const mockMessages = new Map<string, ProtocolMessage[]>();

function recordRunEvent(
  runId: string,
  type: import("../types/ipc").RunEvent["type"],
  data: import("../types/ipc").RunEvent["data"],
): void {
  const events = mockRunEvents.get(runId) ?? [];
  events.push({ id: `evt_${runId}_${events.length + 1}`, run_id: runId,
    seq: events.length + 1, type, created_at: new Date().toISOString(),
    data: structuredClone(data) });
  mockRunEvents.set(runId, events);
}

const MOCK_DEFAULT_ENVELOPE: DataExportEnvelope = {
  format: "minimax-code-export",
  schema_version: 1,
  app_version: "0.0.0-mock",
  exported_at: "1970-01-01T00:00:00Z",
  counts: { sessions: 0, messages: 0 },
  tables: { sessions: [], messages: [] },
};

export function resetMockMcpServers(): void {
  mockMcpServers.clear();
}

export function resetMockMemories(): void {
  mockMemories.clear();
}
const mockHosts = new Map<string, import("../types/ipc").RemoteHost>();
const mockHostTarget = {version:1, policy_version:1, mode:"host", profile_id:"native-host"} as const;
const mockHostTargetHash = "094561ef2e4b5cf95b780c57bd740ae1a2cf62fa05bc7711f87cc54272a78d16";
function validateMockExecutionProfile(value: unknown): void {
  if (value !== undefined && value !== null && (typeof value !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/.test(value)))
    throw new Error("execution_profile_id must be a profile ID or null");
}

function requireMockHost(id: string): import("../types/ipc").RemoteHost {
  const host = mockHosts.get(id);
  if (!host) throw new Error(`unknown host_id: ${id}`);
  return host;
}

function mockHandle(
  method: string,
  params: unknown,
  client: IPCClient,
  id: JsonRpcId | null,
): unknown {
  const input = params as Record<string, unknown> | undefined;
  if (method === "session.update" && input && ["engine", "engine_thread_id", "engine_state"].some((key) => key in input)) {
    throw new Error("Session engine and thread binding are immutable");
  }
  if ((method === "message.update" || method === "message.delete") && input) {
    const owner = [...mockMessages.entries()].find(([, messages]) => messages.some((message) => message.id === input.message_id));
    if (owner && mockSessions.get(owner[0])?.engine === "codex") throw new Error("Codex history is immutable; create a new session with a handoff");
  }
  switch (method) {
    case "host.create": {
      const p = params as import("../types/ipc").HostConfig;
      if ([...mockHosts.values()].some((host) => host.name === p.name)) {
        throw new Error(`host name already taken: ${p.name}`);
      }
      if (p.remote_port != null && (!Number.isInteger(p.remote_port) || p.remote_port < 1 || p.remote_port > 65535)) throw new Error("port must be 1..65535");
      const now = new Date().toISOString();
      const host: import("../types/ipc").RemoteHost = {
        ...p, id: `mock-host-${crypto.randomUUID()}`, port: p.port ?? 22, remote_port: p.remote_port ?? 8765,
        identity_file: p.identity_file ?? "", enabled: true, agent_version: "",
        created_at: now, updated_at: now, last_probe_at: null, last_probe_ok: null,
      };
      mockHosts.set(host.id, host);
      return { host };
    }
    case "host.list": {
      const p = params as { enabled?: boolean };
      return { hosts: [...mockHosts.values()].filter((host) => p?.enabled == null || host.enabled === p.enabled) };
    }
    case "host.get": {
      return { host: requireMockHost((params as { host_id: string }).host_id) };
    }
    case "host.update": {
      const { host_id, ...fields } = params as Partial<import("../types/ipc").HostConfig> & { host_id: string; enabled?: boolean };
      if (fields.remote_port != null && (!Number.isInteger(fields.remote_port) || fields.remote_port < 1 || fields.remote_port > 65535)) throw new Error("port must be 1..65535");
      const host = { ...requireMockHost(host_id), ...fields, updated_at: new Date().toISOString() };
      mockHosts.set(host_id, host);
      return { ok: true, host };
    }
    case "host.delete": {
      const p = params as { host_id: string };
      requireMockHost(p.host_id);
      mockHosts.delete(p.host_id);
      return { ok: true, host_id: p.host_id };
    }
    case "host.probe": {
      const host = requireMockHost((params as { host_id: string }).host_id);
      if (!host.enabled) throw new Error("host is disabled");
      host.last_probe_at = new Date().toISOString();
      host.last_probe_ok = false;
      return { host, probe: { ok: false, timed_out: false, agent_version: "", os: "", python_version: "", error: "SSH is unavailable in mock mode" } };
    }
    case "host.connect":
    case "host.disconnect":
    case "host.connection": {
      const { host_id } = params as { host_id: string };
      requireMockHost(host_id);
      return { connection: { host_id, state: method === "host.connect" ? "failed" : "disconnected", local_url: null, remote_port: null, error: method === "host.connect" ? "SSH is unavailable in mock mode" : null } };
    }
    case "host.service_autostart": {
      const p = params as { host_id: string };
      if (!requireMockHost(p.host_id).enabled) throw new Error("host is disabled");
      return { ok: false, timed_out: false };
    }
    case "host.service_logs": {
      const p = params as { host_id: string };
      if (!requireMockHost(p.host_id).enabled) throw new Error("host is disabled");
      return { ok: false, reason: "unavailable_or_empty", text: null, truncated: false };
    }
    case "host.service_status": {
      const p = params as { host_id: string };
      if (!requireMockHost(p.host_id).enabled) throw new Error("host is disabled");
      return { ok: false, reason: "unavailable", status: null };
    }
    case "host.service_start": {
      const p = params as { host_id: string };
      if (!requireMockHost(p.host_id).enabled) throw new Error("host is disabled");
      return { ok: false, exit_code: null, stderr: "SSH is unavailable in mock mode", timed_out: false };
    }
    case "host.exec":
    case "host.push":
    case "host.fetch":
    case "host.transfer_status":
    case "host.transfer_status_bound": {
      const p = params as { host_id: string; remote_path: string; local_path: string; transfer_id?: string };
      if (!requireMockHost(p.host_id).enabled) throw new Error("host is disabled");
      const failure = { ok: false, exit_code: null, stderr: "SSH is unavailable in mock mode" };
      if (method === "host.exec") return { ...failure, stdout: "", timed_out: false };
      if (method === "host.fetch") return { ...failure, local_path: p.local_path };
      return { ...failure, remote_path: p.remote_path, transfer_id: p.transfer_id ?? null, publication_state: "unknown", backup_path: null };
    }
    case "ping": {
      // If the caller passed a request id, also synthesize a streaming
      // chunk so the UI can verify its event wiring.
      if (id != null) {
        const sid = `mock-ses-${Date.now()}`;
        const mid = `mock-msg-${Date.now()}`;
        const fullText = "Hello from mock backend! (no Tauri sidecar)";
        const chunks = fullText.match(/.{1,8}/g) ?? [fullText];
        chunks.forEach((delta, i) => {
          setTimeout(() => {
            client._emit(StreamEvent.MessageChunk, {
              session_id: sid,
              message_id: mid,
              delta,
              done: i === chunks.length - 1,
            } satisfies MessageChunkData);
          }, 5 * i);
        });
      }
      return { pong: Date.now(), uptime_s: 0, server: "mock" };
    }

    case "session.get": {
      const p = params as { session_id: string };
      const session = mockSessions.get(p.session_id);
      if (!session) throw new Error("Unknown session");
      return { session: structuredClone(session), recent_messages: [] };
    }
    case "session.create": {
      const p = params as {
        engine?: "native" | "codex";
        title?: string;
        reuse_empty_session_id?: string;
        expected_service_id?: string;
        handoff_source_session_id?: string;
        handoff_source_workspace_mode?: "local" | "worktree";
        handoff_source_workspace_path?: string;
        handoff_target_project_root?: string;
        project_id?: string;
        workspace_mode?: "local" | "worktree";
        workspace_path?: string;
        worktree_branch?: string;
        base_branch?: string;
      } | undefined;
      if (p?.handoff_source_session_id) {
        const source = mockSessions.get(p.handoff_source_session_id);
        const custom = source && ((source.workspace_mode ?? "local") !== "local" || !!source.workspace_path);
        const project = mockProjects.get(p.project_id ?? "inbox");
        if (p.expected_service_id !== "mock" || !source || source.archived ||
            (source.project_id ?? "inbox") !== (p.project_id ?? "inbox") || (source.engine ?? "native") === (p.engine ?? "native") ||
            p.reuse_empty_session_id || (p.workspace_mode && p.workspace_mode !== "local") || p.workspace_path || p.worktree_branch || p.base_branch ||
            (custom && (!source.workspace_path || p.handoff_source_workspace_mode !== (source.workspace_mode ?? "local") ||
              p.handoff_source_workspace_path !== source.workspace_path || !project?.root_path || p.handoff_target_project_root !== project.root_path))) throw new Error("Handoff source, project or workspace mismatch");
      }
      const reusable = p?.reuse_empty_session_id
        ? mockSessions.get(p.reuse_empty_session_id)
        : undefined;
      if (
        reusable
        && (reusable.engine ?? "native") === (p?.engine ?? "native")
        && !reusable.archived
        && reusable.workspace_mode === "local"
        && !mockSessionsWithMessages.has(reusable.id)
      ) {
        reusable.title = p?.title ?? DEFAULT_SESSION_TITLE;
        reusable.updated_at = Date.now();
        return { session_id: reusable.id, session: reusable, reused: true };
      }

      const sid = `ses_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const session: Session = {
        id: sid,
        engine: p?.engine ?? "native",
        title: p?.title ?? DEFAULT_SESSION_TITLE,
        archived: false,
        created_at: now,
        updated_at: now,
        project_id: p?.project_id ?? "inbox",
        model_id: null,
        workspace_mode: p?.workspace_mode ?? "local",
        workspace_path: p?.workspace_path ?? null,
        worktree_branch: p?.worktree_branch ?? null,
        base_branch: p?.base_branch ?? null,
      };
      mockSessions.set(sid, session);
      return { session_id: sid, session, reused: false };
    }

    case "workspace.create_worktree_session": {
      const sid = `ses_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const p = params as { title?: string; base_ref?: string; project_id?: string } | undefined;
      const session: Session = {
        id: sid,
        title: p?.title ?? DEFAULT_WORKTREE_TITLE,
        archived: false,
        created_at: now,
        updated_at: now,
        model_id: null,
        project_id: p?.project_id ?? "inbox",
        workspace_mode: "worktree",
        workspace_path: `/tmp/minimax-code/worktrees/${sid}`,
        worktree_branch: null,
        base_branch: p?.base_ref ?? "HEAD",
      };
      mockSessions.set(sid, session);
      return { session_id: sid, session, worktree_path: session.workspace_path ?? undefined };
    }

    case "workspace.list_worktrees": {
      return {
        sessions: Array.from(mockSessions.values())
          .filter((s) => s.workspace_mode === "worktree")
          .sort((a, b) => b.updated_at - a.updated_at),
      };
    }

    case "workspace.delete_worktree": {
      const sid = (params as { session_id: string }).session_id;
      const s = mockSessions.get(sid);
      if (s) {
        s.workspace_mode = "local";
        s.workspace_path = null;
        s.worktree_branch = null;
        s.base_branch = null;
        s.updated_at = Date.now();
      }
      return { ok: true, session: s };
    }

    case "session.list": {
      const p = params as { archived?: boolean; project_id?: string; limit?: number; offset?: number; search?: string } | undefined;
      const search = p?.search?.trim().toLowerCase() ?? "";
      const filtered = Array.from(mockSessions.values())
        .filter((s) => (p?.archived === undefined ? true : s.archived === Boolean(p.archived)))
        .filter((s) => (p?.project_id === undefined ? true : s.project_id === p.project_id))
        .filter((s) => {
          if (!search) return true;
          return s.title.toLowerCase().includes(search) || s.id.toLowerCase().includes(search);
        })
        .sort((a, b) => b.updated_at - a.updated_at);
      const offset = p?.offset ?? 0;
      const limit = p?.limit ?? filtered.length;
      return {
        sessions: filtered.slice(offset, offset + limit),
        total: filtered.length,
      };
    }

    case "session.archive":
    case "session.unarchive":
    case "session.delete": {
      const sid = (params as { session_id: string }).session_id;
      if (method === "session.delete") mockSessions.delete(sid);
      else {
        const s = mockSessions.get(sid);
        if (s) {
          s.archived = method === "session.archive";
          s.updated_at = Date.now();
        }
      }
      return { ok: true };
    }

    case "session.update": {
      const p = params as { session_id: string; title?: string };
      const s = mockSessions.get(p.session_id);
      if (!s) return { ok: false, session: null };
      if (p.title !== undefined) {
        s.title = p.title;
        s.updated_at = Date.now();
      }
      return { ok: true, session: s };
    }

    case "session.updateProject": {
      const p = params as { session_id: string; project_id: string };
      const s = mockSessions.get(p.session_id);
      const project = mockProjects.get(p.project_id);
      if (!s) return { ok: false, session: null };
      if (!project) return { ok: false, session: null };
      s.project_id = p.project_id;
      s.updated_at = Date.now();
      return { ok: true, session: s };
    }

    case "session.batchArchive": {
      const p = params as { session_ids: string[]; archived: boolean };
      const sessions: import("../types/ipc").Session[] = [];
      for (const sid of p.session_ids) {
        const s = mockSessions.get(sid);
        if (s) {
          s.archived = p.archived;
          s.updated_at = Date.now();
          sessions.push(s);
        }
      }
      return { ok: true, session_ids: p.session_ids, sessions, updated: sessions.length };
    }

    case "session.batchUpdateProject": {
      const p = params as { session_ids: string[]; project_id: string };
      const project = mockProjects.get(p.project_id);
      const sessions: import("../types/ipc").Session[] = [];
      if (project) {
        for (const sid of p.session_ids) {
          const s = mockSessions.get(sid);
          if (s) {
            s.project_id = p.project_id;
            s.updated_at = Date.now();
            sessions.push(s);
          }
        }
      }
      return {
        ok: true,
        project_id: p.project_id,
        session_ids: p.session_ids,
        sessions,
        updated: sessions.length,
      };
    }

    case "session.stats": {
      const sessions = Array.from(mockSessions.values());
      return {
        total_sessions: sessions.length,
        archived_sessions: sessions.filter((s) => s.archived).length,
        total_messages: mockSessionsWithMessages.size,
      } satisfies SessionStatsResult;
    }

    case "session.export": {
      const p = params as { session_id: string };
      const s = mockSessions.get(p.session_id);
      const title = s?.title ?? "Untitled session";
      return {
        markdown: `# ${title}\n\n<!-- session_id: ${p.session_id} -->\n\n## Assistant\n\nExported from MiniMax Code (mock mode).`,
      } satisfies SessionExportResult;
    }

    case "artifact.candidates":
      throw new Error("演示模式不提供任务文件候选，请连接真实后端。");
    case "artifact.register":
    case "artifact.list":
    case "artifact.get": {
      const p = (params ?? {}) as Record<string, unknown>;
      return artifactMock(method, p, mockProjects.has(String(p.project_id)));
    }
    case "attachment.ocr_pdf_page":
    case "attachment.ocr_image":
    case "attachment.read_document":
      throw new Error("演示模式不支持读取文档，请连接真实后端。");
    case "attachment.list_xlsx_sheets":
    case "attachment.extract_xlsx":
      throw new Error("演示模式不支持读取 XLSX，请连接真实后端。");
    case "attachment.generate_report_pair":
    case "attachment.generate_pdf":
    case "attachment.generate_docx":
      throw new Error("演示模式不生成报告；请连接真实后端。");
    case "attachment.list":
    case "attachment.get": {
      const p = (params ?? {}) as Record<string, unknown>;
      return attachmentMock(method, p, mockProjects.has(String(p.project_id)));
    }
    case "office.list": {
      const p = (params ?? {}) as { project_id?: string };
      if (!p.project_id || !mockProjects.has(p.project_id)) throw new Error("An existing project is required");
      return { deliveries: [], service_id: "mock-office-unavailable", next_cursor: null };
    }
    case "office.generate_analysis":
    case "office.generate_xlsx":
    case "office.get":
      throw new Error("Office generation and verification require the real backend; unavailable in mock mode");
    case "project.list": {
      const p = params as { archived?: boolean } | undefined;
      const projects = Array.from(mockProjects.values())
        .filter((proj) => (p?.archived === undefined ? true : proj.archived === Boolean(p.archived)))
        .sort((a, b) => b.updated_at - a.updated_at);
      return { projects };
    }

    case "project.create": {
      const p = params as { name: string; description?: string; root_path?: string; validate_command?: string };
      const pid = `proj_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const project = {
        id: pid,
        name: p.name,
        description: p.description ?? "",
        root_path: p.root_path ?? "",
        validate_command: p.validate_command ?? "",
        archived: false,
        created_at: now,
        updated_at: now,
      };
      mockProjects.set(pid, project);
      return { project };
    }

    case "project.update": {
      const p = params as { project_id: string; name?: string; description?: string; root_path?: string; validate_command?: string };
      const project = mockProjects.get(p.project_id);
      if (!project) return { ok: false, project: null };
      if (p.name !== undefined) project.name = p.name;
      if (p.description !== undefined) project.description = p.description;
      if (p.root_path !== undefined) project.root_path = p.root_path;
      if (p.validate_command !== undefined) project.validate_command = p.validate_command;
      project.updated_at = Date.now();
      return { ok: true, project };
    }

    case "engine.list": {
      // M4 — mock cards: native always available, codex reported from
      // the environment the same way the real probe would.
      const codexAvailable = false;
      return {
        default: "native",
        engines: [
          {
            id: "native",
            name: "Native (built-in Python agent)",
            available: true,
            unavailable_reason: null,
            capabilities: { streaming: true, resume: true, cancel: true,
                            approvals: true, usage: true, steer: true },
            version: "mock",
          },
          {
            id: "codex",
            name: "Codex (OpenAI Codex CLI)",
            available: codexAvailable,
            unavailable_reason: codexAvailable ? null : "codex CLI not found on PATH (mock)",
            capabilities: { streaming: true, resume: true, cancel: true,
                            approvals: true, usage: true, steer: true },
            version: codexAvailable ? "mock" : null,
          },
        ],
      };
    }

    case "engine.status": {
      const p = params as { engine_id: string };
      const list = mockHandle("engine.list", {} as unknown, undefined as never, null) as import("../types/ipc").ListEnginesResult;
      const engine = list.engines.find((e) => e.id === p.engine_id);
      if (!engine) throw new Error(`unknown engine_id: ${p.engine_id}`);
      return { engine };
    }

    case "project.delete": {
      const p = params as { project_id: string };
      const pid = p.project_id;
      for (const s of mockSessions.values()) {
        if (s.project_id === pid) s.project_id = "inbox";
      }
      mockProjects.delete(pid);
      return { ok: true, project_id: pid };
    }

    case "project.archive":
    case "project.unarchive": {
      const p = params as { project_id: string };
      const project = mockProjects.get(p.project_id);
      if (!project) return { ok: false, project: null };
      project.archived = method === "project.archive";
      project.updated_at = Date.now();
      return { ok: true, project };
    }

    case "message.list": {
      const p = params as { session_id: string; limit?: number; before?: string };
      const rows = mockMessages.get(p.session_id) ?? [];
      const filtered = p.before ? rows.filter((message) => message.created_at < Date.parse(p.before!)) : rows;
      return { messages: structuredClone(filtered.slice(-(p.limit ?? 100))) };
    }

    case "message.update": {
      const p = params as { message_id: string; content?: string };
      const saved = [...mockMessages.values()].flat().find((message) => message.id === p.message_id);
      if (saved) saved.text = p.content ?? "";
      return {
        ok: true,
        message: {
          id: p.message_id,
          role: "user",
          text: p.content ?? "",
          created_at: Date.now(),
        },
      } satisfies UpdateMessageResult;
    }

    case "message.delete": {
      const p = params as { message_id: string };
      for (const [sid, rows] of mockMessages) mockMessages.set(sid, rows.filter((message) => message.id !== p.message_id));
      return { ok: true, message_id: p.message_id } satisfies DeleteMessageResult;
    }

    case "run.project_summary":
      throw new Error("演示模式不提供持久项目运行摘要");
    case "run.list": {
      const p = params as { session_id?: string; status?: string; mode?: string; limit?: number; offset?: number; include_active?: boolean; recovery_lookup?: import("../types/ipc").RunRecoveryLookup } | undefined;
      if (p?.recovery_lookup) {
        if (Object.keys(p).length !== 1) throw new Error("recovery_lookup cannot be combined with list filters");
        const q = p.recovery_lookup, source = mockRuns.get(q.run_id)?.run, session = mockSessions.get(q.session_id);
        if (q.service_id !== "mock" || !q.idempotency_key || !source || source.session_id !== q.session_id ||
            !session || (session.project_id ?? null) !== q.project_id || (source.metadata?.project_id ?? null) !== q.project_id)
          throw new Error("Recovery lookup scope changed");
        const runs = Array.from(mockRuns.values()).map((entry) => entry.run).filter((run) =>
          run.session_id === q.session_id && run.resumed_from_id === q.run_id && run.request_key === q.idempotency_key);
        if (runs.some((run) => (run.metadata?.project_id ?? null) !== q.project_id)) throw new Error("Recovery child project changed");
        return { runs: structuredClone(runs) } satisfies ListRunsResult;
      }
      let runs = Array.from(mockRuns.values()).reverse().map((r) => r.run);
      if (p?.session_id) runs = runs.filter((r) => r.session_id === p.session_id);
      runs.sort((a, b) => b.created_at.localeCompare(a.created_at));
      const offset = p?.offset ?? 0;
      const limit = p?.limit ?? 20;
      if (p?.include_active) {
        if (!p.session_id || p.status || p.mode || offset || limit < 1 || limit > 100) throw new Error("invalid recovery session or limit");
        const active = runs.filter((r) => ["queued", "planning", "running", "awaiting_approval", "waiting_input"].includes(r.status));
        const ids = new Set([...runs.slice(0, limit), ...active.slice(0, 101)].map((r) => r.id));
        return { runs: runs.filter((r) => ids.has(r.id)), active_truncated: active.length > 100 } satisfies ListRunsResult;
      }
      if (p?.status) runs = runs.filter((r) => r.status === p.status);
      if (p?.mode) runs = runs.filter((r) => r.mode === p.mode);
      return { runs: runs.slice(offset, offset + limit) } satisfies ListRunsResult;
    }

    case "run.steps": {
      const runId = (params as { run_id: string }).run_id;
      const entry = mockRuns.get(runId);
      if (!entry) throw new Error("unknown run_id");
      return {
        run: entry.run,
        steps: entry.steps,
        cursor: mockRunEvents.get(runId)?.length ?? 0,
        queued_input: null,
        pending_ask_user: null,
        pending_ask_users: [],
        pending_native_permissions: [],
        pending_codex_permissions: [],
        live_stream: null,
      };
    }

    case "budget.settings_get":
    case "budget.settings_set":
      throw new Error("演示模式不提供真实预算设置或账本");
    case "budget.get":
      throw new Error("模拟模式没有实际预算账本；未知用量不能按零处理。");
    case "budget.reconcile":
      throw new Error("模拟模式不支持核对真实预算责任。");
    case "run.events": {
      const p = params as { run_id: string; after_seq?: number; limit?: number };
      if (!p || !mockRuns.has(p.run_id)) throw new Error("unknown run_id");
      const after = p.after_seq ?? 0;
      const limit = p.limit ?? 100;
      const events = mockRunEvents.get(p.run_id) ?? [];
      if (!Number.isSafeInteger(after) || after < 0 || after > events.length ||
          !Number.isInteger(limit) || limit < 1 || limit > 500) {
        throw new Error("invalid run.events cursor or limit");
      }
      const page = events.slice(after, after + limit);
      const next = page.at(-1)?.seq ?? after;
      return { events: structuredClone(page), next_seq: next, has_more: next < events.length };
    }

    case "run.start": {
      const p = params as import("../types/ipc").RunStartParams;
      const pinned = mockSessions.get(p.session_id)?.engine ?? "native";
      if (p.engine && p.engine !== pinned) throw new Error("Requested engine differs from pinned session engine");
      if (pinned === "codex") throw new Error("Codex CLI unavailable in mock mode; external inference is never simulated");
      if (!p.session_id || !p.idempotency_key || (p.engine && p.engine !== "native")) {
        throw new Error("run.start requires a session and idempotency key, with native engine");
      }
      const created = !mockRequestKeys.has(p.session_id + "\0" + p.idempotency_key);
      const reply = mockHandle("agent.send_message", p, client, id) as import("../types/ipc").SendMessageResult;
      return { run: structuredClone(mockRuns.get(reply.run_id!)!.run), created };
    }
    case "run.research_search":
    case "run.research_read":
      throw new Error("演示模式无法访问公网或保存真实来源");
    case "run.research_list":
    case "run.research_get":
      throw new Error("演示模式没有已读取的真实网页来源");
    case "run.child_workspace":
      throw new Error("演示模式没有真实子任务工作区");
    case "run.verify_collected":
      throw new Error("演示模式不能运行项目验证命令");
    case "run.input_image":
      throw new Error("演示模式没有已保存的 Codex 输入图片");
    case "run.output_page":
      throw new Error("演示模式没有已保存的工具输出");
    case "run.output_inventory":
    case "run.output_clear":
      throw new Error("演示模式没有真实工具输出存储");
    case "run.child_list": {
      const p = params as { session_id: string };
      const session = mockSessions.get(p.session_id);
      if (!session) throw new Error("Unknown parent session");
      return { session_id: p.session_id, project_id: session.project_id ?? null, service_id: "mock", children: [], limit: 100 };
    }
    case "run.steer":
      throw new Error("演示模式不支持运行中补充");
    case "run.get": {
      const runId = (params as { run_id: string }).run_id;
      const entry = mockRuns.get(runId);
      if (!entry) throw new Error("unknown run_id");
      return { run: structuredClone(entry.run), result: structuredClone(mockRunReceipts.get(runId) ?? null) };
    }
    case "run.cancel": {
      if ((params as { schedule_scope?: unknown }).schedule_scope) throw new Error("Mock 模式不执行定时运行控制");
      const runId = (params as { run_id: string }).run_id;
      const entry = mockRuns.get(runId);
      if (!entry) throw new Error("unknown run_id");
      if (["completed", "failed", "cancelled", "interrupted"].includes(entry.run.status)) {
        return { ok: true, requested: false, run: structuredClone(entry.run) };
      }
      entry.run = { ...entry.run, status: "cancelled", completed_at: new Date().toISOString() };
      entry.steps = entry.steps.map((step) => {
        if (!["pending", "running"].includes(step.status)) return step;
        const stopped = { ...step, status: "cancelled" as const, completed_at: entry.run.completed_at };
        recordRunEvent(runId, "step_updated", { step: stopped });
        return stopped;
      });
      mockRunReceipts.set(runId, { session_id: entry.run.session_id, message_id: entry.run.assistant_message_id!,
        run_id: runId, text: "", cancelled: true });
      recordRunEvent(runId, "status", { run: entry.run });
      client._emit(StreamEvent.RunCompleted, { run: structuredClone(entry.run) });
      return { ok: true, requested: true, run: structuredClone(entry.run) };
    }
    case "run.resume": {
      const p = params as { run_id: string; idempotency_key: string; reviewed?: boolean; handoff?: string; budget_tokens?: number };
      if (p.budget_tokens !== undefined) throw new Error("模拟模式没有实际预算账本，不能修改累计预算");
      if (p.handoff !== undefined && (typeof p.handoff !== "string" || p.handoff.length > 4000)) throw new Error("Invalid recovery handoff");
      const entry = mockRuns.get(p.run_id);
      const original = mockRunRequests.get(p.run_id);
      const continuation = !!entry && entry.run.status === "completed" && entry.run.metadata?.truncated === true && entry.run.metadata?.engine !== "codex";
      if (!entry || !original || (!continuation && !["interrupted", "failed", "cancelled"].includes(entry.run.status))) {
        throw new Error("Run cannot be resumed; start a new task");
      }
      if ((continuation || entry.run.started_at) && !p.reviewed) throw new Error("Review prior steps and workspace before resuming");
      const note = `\n\n[Runtime recovery context: prior run ${entry.run.id} ended as ${entry.run.status}. Continue the original task after inspecting the existing workspace and prior conversation. Verify which actions already took effect before repeating a write. No tool step has been automatically retried.]`;
      const handoff = p.handoff?.trim() ? `\n\nUser recovery handoff (reviewed by the user):\n${p.handoff}` : "";
      const content = continuation ? `[Continue truncated run ${entry.run.id}. Inspect the existing workspace and prior conversation, verify what has already happened, and continue only unfinished work. Do not blindly repeat unconfirmed writes.]${note}${handoff}`
        : typeof original.content === "string" ? original.content + note + handoff
        : [...original.content, { type: "text" as const, text: note + handoff }];
      const result = mockHandle("run.start", { ...original, content, idempotency_key: p.idempotency_key }, client, id) as import("../types/ipc").RunAdmissionResult;
      mockRuns.get(result.run.id)!.run.resumed_from_id = p.run_id;
      return { ...result, run: structuredClone(mockRuns.get(result.run.id)!.run) };
    }

    case "agent.send_message": {
      const p = params as import("../types/ipc").SendMessageParams;
      if (p.mode && !["chat", "plan", "execute"].includes(p.mode)) {
        throw new Error("mode must be chat, plan, or execute");
      }
      const sid = p.session_id ?? `ses_${Math.random().toString(36).slice(2, 10)}`;
      if (mockSessions.get(sid)?.engine === "codex") throw new Error("Codex sessions must send through run.start");
      const requestKey = p.idempotency_key ? sid + "\0" + p.idempotency_key : null;
      if (p.restrict_network_tools !== undefined && typeof p.restrict_network_tools !== "boolean") throw new Error("restrict_network_tools must be a boolean");
      const signature = JSON.stringify({ content: p.content, mode: p.mode ?? "chat", restrict_network_tools: p.restrict_network_tools === true });
      const previous = requestKey ? mockRequestKeys.get(requestKey) : null;
      if (previous) {
        if (previous.signature !== signature) throw new Error("idempotency_key already belongs to different input");
        const run = mockRuns.get(previous.runId)!.run;
        return structuredClone(mockRunReceipts.get(run.id) ?? { session_id: sid,
          message_id: run.assistant_message_id!, run_id: run.id, text: "", cancelled: false });
      }
      mockSessionsWithMessages.add(sid);
      const mid = `msg_${Math.random().toString(36).slice(2, 10)}`;
      const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
      const userId = `msg_${Math.random().toString(36).slice(2, 10)}`;
      const nowIso = new Date().toISOString();
      const run: import("../types/ipc").AgentRun = {
        id: runId,
        session_id: sid,
        mode: p.mode ?? "chat",
        metadata: { restrict_network_tools: p.restrict_network_tools === true, model_snapshot: { engine: "native", model: mockModels[0].id,
          provider_id: mockModels[0].provider_id ?? null, protocol: mockModels[0].protocol ?? null,
          reasoning_effort: mockReasoningEffort, context_window: mockModels[0].context_window } },
        status: "running",
        title: String(p.content).slice(0, 32),
        assistant_message_id: mid,
        user_message_id: userId,
        request_key: p.idempotency_key,
        created_at: nowIso,
        started_at: nowIso,
      };
      const thinkingStep: import("../types/ipc").AgentRunStep = {
        id: `step_${Math.random().toString(36).slice(2, 10)}`,
        run_id: runId,
        session_id: sid,
        kind: "thought",
        status: "running",
        title: "Thinking",
        summary: "Iteration 1",
        payload: null,
        started_at: nowIso,
        ordinal: 1,
      };
      mockRuns.set(runId, { run, steps: [thinkingStep] });
      mockMessages.set(sid, [...(mockMessages.get(sid) ?? []), {
        id: userId, role: "user", text: typeof p.content === "string" ? p.content : JSON.stringify(p.content),
        streaming: false, created_at: Date.parse(nowIso), metadata: { run_id: runId },
      }]);
      mockRunRequests.set(runId, structuredClone(p));
      if (requestKey) mockRequestKeys.set(requestKey, { signature, runId });
      recordRunEvent(runId, "created", { run });
      recordRunEvent(runId, "step_created", { step: thinkingStep });
      setTimeout(() => client._emit(StreamEvent.RunCreated, { run }), 0);
      setTimeout(() => client._emit(StreamEvent.RunStepStarted, { run_id: runId, step: thinkingStep }), 8);
      const reply = `(mock reply) Received: ${p.content}`;
      const chunks = reply.match(/.{1,12}/g) ?? [reply];
      chunks.forEach((delta, i) => {
        setTimeout(() => {
          if (mockRuns.get(runId)?.run.status === "cancelled") return;
          client._emit(StreamEvent.MessageChunk, {
            session_id: sid,
            message_id: mid,
            delta,
            done: i === chunks.length - 1,
            metadata: { run_id: runId, thinking_count: 0, tokens_in: 0, tokens_out: 0 },
            run_id: runId,
          } satisfies MessageChunkData);
          if (i === chunks.length - 1) {
            const completedAt = new Date().toISOString();
            const completedStep = {
              ...thinkingStep,
              status: "completed" as const,
              summary: "Done thinking",
              completed_at: completedAt,
              duration_ms: 100,
            };
            const finalStep: import("../types/ipc").AgentRunStep = {
              id: `step_${Math.random().toString(36).slice(2, 10)}`,
              run_id: runId,
              session_id: sid,
              kind: "final",
              status: "completed",
              title: "Final response",
              summary: reply,
              payload: { iterations: 1 },
              started_at: completedAt,
              completed_at: completedAt,
              duration_ms: 0,
              ordinal: 2,
            };
            const completedRun = {
              ...run,
              status: "completed" as const,
              completed_at: completedAt,
              metadata: { ...run.metadata, iterations: 1, memory_count: 0, verification: { version: 1, verdict: "unknown", reason: "not_configured", requires_review: false } },
            };
            mockRuns.set(runId, { run: completedRun, steps: [completedStep, finalStep] });
            mockMessages.set(sid, [...(mockMessages.get(sid) ?? []), {
              id: mid, role: "assistant", text: reply, streaming: false, created_at: Date.parse(completedAt),
              metadata: { run_id: runId, thinking_count: 0, tokens_in: 0, tokens_out: 0, verification: { version: 1, verdict: "unknown", reason: "not_configured", requires_review: false } },
            }]);
            mockRunReceipts.set(runId, { session_id: sid, message_id: mid, run_id: runId, run_status: "completed", text: reply, cancelled: false, verification: { version: 1, verdict: "unknown", reason: "not_configured", requires_review: false } });
            recordRunEvent(runId, "step_updated", { step: completedStep });
            recordRunEvent(runId, "step_created", { step: finalStep });
            recordRunEvent(runId, "status", { run: completedRun });
            client._emit(StreamEvent.RunStepCompleted, { run_id: runId, step: completedStep });
            client._emit(StreamEvent.RunStepCompleted, { run_id: runId, step: finalStep });
            client._emit(StreamEvent.RunCompleted, { run: completedRun });
          }
        }, 12 * i);
      });
      return { session_id: sid, message_id: mid, run_id: runId, text: "", cancelled: false };
    }

    case "agent.cancel":
      return { ok: true };

    case "agent.answer_user": {
      // Mock has no suspended ask_user waits — every request_id is
      // unknown/expired from the mock backend's point of view.
      const p = params as { request_id?: string; answers?: unknown[] };
      if (!p || typeof p !== "object" || typeof p.request_id !== "string") {
        throw new Error("invalid params: 'request_id' is required");
      }
      if (!Array.isArray(p.answers) || p.answers.length === 0) {
        throw new Error("invalid params: 'answers' must be a non-empty array");
      }
      return { ok: false, error: "unknown or expired request_id (mock backend)" };
    }

    case "agent.continue_run": {
      const p = params as import("../types/ipc").ContinueRunParams;
      const source = p.run_id ? mockRuns.get(p.run_id)?.run :
        [...mockRuns.values()].reverse().find((entry) => entry.run.session_id === p.session_id)?.run;
      if (!source || source.session_id !== p.session_id || !source.metadata?.truncated ||
          !["completed", "cancelled"].includes(source.status)) {
        return { ok: false, error: "no truncated run to continue" };
      }
      const reply = mockHandle(
        "agent.send_message",
        { session_id: p.session_id, content: `[continue] (mock) resuming run ${source.id}`,
          idempotency_key: `continue:${source.id}`, mode: source.mode },
        client,
        id,
      ) as import("../types/ipc").SendMessageResult;
      if (!source.metadata.continued) {
        source.metadata = { ...source.metadata, continued: true };
        recordRunEvent(source.id, "status", { run: source });
      }
      mockRuns.get(reply.run_id!)!.run.resumed_from_id = source.id;
      return reply;
    }

    case "skill.enable":
    case "skill.disable":
    case "schedule.delete":
    case "agent.delete":
      return { ok: true };

    case "permission.delete": {
      // Deleting the user rule for a pattern falls back to the
      // factory default — same semantics as the real store.
      const p = params as { tool_pattern: string };
      const idx = mockPermissionRules.findIndex(
        (r) => r.tool_pattern === p.tool_pattern,
      );
      if (idx !== -1) mockPermissionRules.splice(idx, 1);
      return { ok: true, deleted: idx !== -1 ? 1 : 0 };
    }

    case "agent.get": {
      const p = params as { name: string };
      const a = mockAgents.find((x) => x.name === p.name);
      return { agent: a ?? null };
    }
    case "agent.create": {
      const p = params as { name: string; system_prompt: string; execution_profile_id?: string | null; skills?: string[]; tool_allowlist?: string[]; model?: string };
      validateMockExecutionProfile(p.execution_profile_id);
      if (p.skills !== undefined && (!Array.isArray(p.skills) || p.skills.some((id) => typeof id !== "string" || !id.trim()) || new Set(p.skills).size !== p.skills.length)) throw new Error("skills require unique non-blank IDs or names");
      const a: AgentInfo = { execution_profile_id:p.execution_profile_id ?? null, skills:p.skills ?? [], tool_allowlist:p.tool_allowlist, model:p.model, id: `agent_${Date.now()}`, name: p.name, description: p.system_prompt.slice(0, 40), enabled: true, system_prompt: p.system_prompt };
      mockAgents.push(a);
      return { agent: a };
    }
    case "agent.update": {
      const p = params as { name: string };
      const a = mockAgents.find((x) => x.name === p.name);
      const fields = p as { name:string; execution_profile_id?:string | null; skills?:string[]; system_prompt?:string; model?:string; tool_allowlist?:string[]; enabled?:boolean };
      validateMockExecutionProfile(fields.execution_profile_id);
      if (fields.skills !== undefined && (!Array.isArray(fields.skills) || fields.skills.some((id) => typeof id !== "string" || !id.trim()) || new Set(fields.skills).size !== fields.skills.length)) throw new Error("skills require unique non-blank IDs or names");
      if (a) {
        for (const key of ["skills", "system_prompt", "model", "tool_allowlist", "enabled", "execution_profile_id"] as const) {
          if (key in fields) Object.assign(a, { [key]:fields[key] });
        }
      }
      return { agent: a ?? null };
    }

    case "model.list":
      return {
        models: mockModels,
        current: mockModels[0].id,
        reasoning_effort: mockReasoningEffort,
      };

    case "model.set_current": {
      const p = params as { model_id?: string; provider_id?: string };
      return {
        current: p.model_id ?? mockModels[0].id,
        provider_id: p.provider_id,
      };
    }

    case "model.set_reasoning_effort": {
      const raw = (params as { reasoning_effort?: string | null }).reasoning_effort;
      const effort =
        raw == null || String(raw).trim() === "" ? null : String(raw).trim();
      mockReasoningEffort = effort;
      return { ok: true as const, reasoning_effort: effort };
    }

    case "skill.list":
      return { skills: mockSkills };

    case "skill.install": {
      const content = String((params as { content?: string }).content ?? "");
      const name = /^---[\s\S]*?^name:\s*([^\s#]+).*?^---/m.exec(content)?.[1] ?? "custom-skill";
      const skill: SkillInfo = {
        id: `${name}:${name}`,
        name,
        description: "Imported custom skill",
        enabled: true,
        builtin: false,
      };
      const index = mockSkills.findIndex((item) => item.name === name);
      if (index >= 0) mockSkills[index] = skill;
      else mockSkills.push(skill);
      return { skill };
    }

    case "skill.uninstall": {
      const skillId = String((params as { skill_id?: string }).skill_id ?? "");
      const index = mockSkills.findIndex((item) => item.id === skillId);
      if (index >= 0) mockSkills.splice(index, 1);
      return { ok: true, skill_id: skillId };
    }

    case "skill.invoke": {
      const p = params as import("../types/ipc").SkillInvokeArgs & { skill_id: string; admission_only?: boolean };
      if (!p.session_id || !p.idempotency_key?.trim() || typeof p.request !== "string" || !p.request.trim())
        throw new Error("skill.invoke requires an explicit Native session, request and stable key");
      const session = mockSessions.get(p.session_id);
      if (!session || session.engine === "codex") throw new Error("skill.invoke requires an existing Native session");
      if (p.expected_service_id && (p.expected_service_id !== "mock" || (session.project_id ?? null) !== (p.expected_project_id ?? null)))
        throw new Error("Skill service, session or project binding changed");
      const key = p.session_id + "\0" + p.idempotency_key;
      const signature = JSON.stringify({ ...p, admission_only: undefined });
      const previous = mockSkillKeys.get(key);
      if (previous) {
        if (previous.signature !== signature) throw new Error("idempotency_key already belongs to different input");
        const entry = mockRuns.get(previous.runId)!;
        return structuredClone(p.admission_only ? { run: entry.run, created: false } : mockRunReceipts.get(previous.runId));
      }
      const skill = mockSkills.find((item) => item.id === p.skill_id || `${item.id}:${item.id}` === p.skill_id);
      if (!skill?.enabled) throw new Error("Skill is missing or disabled");
      const runId = `run_skill_${crypto.randomUUID()}`;
      const mid = `msg_${crypto.randomUUID()}`, now = new Date().toISOString();
      const text = `[mock] Skill ${p.skill_id} processed request: ${p.request}`;
      const run: import("../types/ipc").AgentRun = { id:runId, session_id:p.session_id, mode:"execute", status:"completed",
        title:p.request.slice(0,32), assistant_message_id:mid, request_key:p.idempotency_key, created_at:now,
        started_at:now, completed_at:now, metadata:{skill_execution:{skill_id:p.skill_id,label:p.label}} };
      const receipt: import("../types/ipc").SkillInvokeResult = { session_id:p.session_id, message_id:mid,
        run_id:runId, skill_id:p.skill_id, status:"completed",run_status:"completed", text,output:text,
        iterations:1,tool_calls:0,cancelled:false,truncated:false,partial:false,
        ...(p.diff ? {comments:[],stats:{files:1,additions:3,deletions:1}} : {}) };
      mockRuns.set(runId,{run,steps:[]});
      mockRunReceipts.set(runId,receipt);
      mockSkillKeys.set(key,{signature,runId});
      recordRunEvent(runId,"created",{run});
      recordRunEvent(runId,"status",{run});
      return structuredClone(p.admission_only ? {run,created:true} : receipt);
    }

    case "schedule.list":
      return { jobs: mockJobs };

    case "schedule.create": {
      const p = params as {name: string; cron_expr: string; payload: {prompt: string}; context: import("../types/ipc").ScheduleContext; enabled?: boolean; expected_service_id?: string; idempotency_key?: string};
      if (p.expected_service_id && p.expected_service_id !== "mock") throw new Error("Service identity changed");
      if (p.idempotency_key !== undefined && (typeof p.idempotency_key !== "string" || !p.idempotency_key.trim() || p.idempotency_key.length > 128)) throw new Error("Invalid idempotency key");
      const signature = JSON.stringify({name:p.name.trim(), cron_expr:p.cron_expr.trim(), payload:p.payload, context:p.context, enabled:p.enabled ?? true});
      const previous = p.idempotency_key ? mockScheduleCreateKeys.get(p.idempotency_key) : undefined;
      if (previous) {
        if (previous.signature !== signature) throw new Error("idempotency_key already belongs to a different schedule request");
        const job = mockJobs.find((item) => item.id === previous.jobId);
        if (!job) throw new Error("Original schedule is unavailable; do not create a replacement automatically");
        return {job, created:false};
      }
      if (!p.context?.project_id || !p.context.provider_id || !p.context.model || !(p.context.budget_tokens > 0) || Object.keys(p.payload).some((key) => key !== "prompt")) throw new Error("Bind local Native context and a positive budget");
      const job: ScheduledJob = {id:crypto.randomUUID(), name:p.name, cron:p.cron_expr, prompt:p.payload.prompt,
        context:p.context, revision:1, enabled:false, binding_state:"blocked", binding_reason:"Mock 模式不执行真实调度",last_run_at:null,next_run_at:null};
      mockJobs.push(job);
      if (p.idempotency_key) mockScheduleCreateKeys.set(p.idempotency_key, {signature, jobId:job.id});
      return {job, ...(p.idempotency_key ? {created:true} : {})};
    }
    case "schedule.update": {
      const p = params as {job_id:string; expected_revision:number; name:string; cron_expr:string; payload:{prompt:string}; context:import("../types/ipc").ScheduleContext};
      const job = mockJobs.find((entry) => entry.id === p.job_id);
      if (!job || job.revision !== p.expected_revision) throw new Error("Schedule revision conflict");
      if (!p.context?.project_id || !p.context.provider_id || !p.context.model || !(p.context.budget_tokens > 0) || Object.keys(p.payload).some((key) => key !== "prompt") || !p.payload.prompt?.trim()) throw new Error("Bind local Native context and a positive budget");
      Object.assign(job,{name:p.name,cron:p.cron_expr,prompt:p.payload.prompt,context:p.context,revision:p.expected_revision+1});
      return {job};
    }
    case "schedule.occurrences": return {occurrences:[], next_cursor:null};
    case "schedule.enable": throw new Error("Mock 模式不执行真实调度");
    case "schedule.disable": {
      const p = params as {job_id:string; expected_service_id?:string; expected_revision?:number; expected_occurrence_id?:string};
      const job = mockJobs.find((entry) => entry.id === p.job_id);
      if (p.expected_service_id !== undefined || p.expected_revision !== undefined || p.expected_occurrence_id !== undefined) {
        if (p.expected_service_id !== "mock" || !job || job.revision !== p.expected_revision || job.active_occurrence_id !== p.expected_occurrence_id || !p.expected_occurrence_id)
          throw new Error("Schedule version or active occurrence changed; refresh before stopping");
      }
      if (job) job.enabled=false;
      return {job:job??null};
    }
    case "schedule.run_now": throw new Error("Mock 模式不执行真实调度");

    case "agent.list":
      return { agents: mockAgents, execution_profiles: [
        {id:"native-host",label:"Native 宿主",mode:"host",available:true,reason:null,unattended_supported:false,boundary_summary:"宿主 Native 进程"},
        {id:"linux-podman-l1",label:"Linux 容器 L1（需人工监督）",mode:"podman",available:false,reason:"Mock 模式不执行真实容器",unattended_supported:false,boundary_summary:"受监督的进程隔离；尚非完整 OS 文件沙盒"},
      ] };

    case "agent.invoke":
    case "agent.spawn_subagent": {
      const p = params as Record<string, unknown>;
      const sid = String(p.session_id ?? p.parent_session_id ?? "");
      if (!sid || !String(p.idempotency_key ?? "").trim() || typeof p.request !== "string" || !p.request.trim())
        throw new Error("Agent invocation requires explicit Native source session, request and stable key");
      if (!mockSessions.has(sid) || mockSessions.get(sid)?.engine === "codex") throw new Error("Select an existing Native session");
      if (["family_id", "parent_run_id", "instructions", "internal_agent"].some((key) => key in p)) throw new Error("Executable authority is server-owned");
      const key = sid + "\0" + p.idempotency_key;
      const signature = JSON.stringify({ ...p, run_id: undefined, admission_only: undefined, method });
      const old = mockAgentKeys.get(key);
      if (old) {
        if (old.signature !== signature) throw new Error("idempotency_key already belongs to different agent input");
        return structuredClone(p.admission_only ? {run: mockRuns.get(old.runId)!.run, created:false} : mockRunReceipts.get(old.runId));
      }
      const agent = mockAgents.find((item) => item.id === p.agent_id || item.name === p.name);
      if (!agent?.enabled) throw new Error("Agent is missing or disabled");
      if (agent.execution_profile_id != null && agent.execution_profile_id !== "native-host")
        throw new Error("Mock 模式下执行配置不可用，不能回退宿主");
      const dependencies = (agent.skills ?? []).map((id) => {
        const exact = mockSkills.find((skill) => skill.id === id);
        const matches = exact ? [exact] : mockSkills.filter((skill) => skill.name === id);
        if (matches.length !== 1 || !matches[0].enabled) throw new Error("Skill dependency is unknown, ambiguous or disabled; use an exact enabled ID");
        return matches[0];
      });
      if (new Set(dependencies.map((skill) => skill.id)).size !== dependencies.length) throw new Error("Duplicate canonical skill dependency");
      const required = dependencies.flatMap((skill) => skill.tools ?? []);
      if (agent.tool_allowlist && required.some((name) => !agent.tool_allowlist!.includes(name))) throw new Error("Agent allowlist excludes required skill tools");
      const execution = `ses_agent_${crypto.randomUUID()}`, rid = `run_agent_${crypto.randomUUID()}`;
      const now = new Date().toISOString(), mid = `msg_${crypto.randomUUID()}`;
      mockSessions.set(execution, {...mockSessions.get(sid)!, id:execution, title:`agent:${agent.name}`});
      const run: import("../types/ipc").AgentRun = {id:rid,session_id:execution,mode:"execute",status:"completed",title:p.request.slice(0,32),
        created_at:now,started_at:now,completed_at:now,assistant_message_id:mid,request_key:String(p.idempotency_key),
        metadata:{agent_execution:{version:3,execution_target:mockHostTarget,execution_target_hash:mockHostTargetHash,execution_installation_id:null,agent_id:agent.id,agent:agent.name,source_session_id:sid,skill_bindings:{version:1,skills:dependencies.map((skill) => ({id:skill.id,version:skill.version ?? "mock",manifest_tools:skill.tools ?? []}))}}}};
      const receipt: import("../types/ipc").SpawnSubagentResult = {run_id:rid,agent_run_id:rid,agent_id:agent.id,agent:agent.name,
        session_id:execution,run_status:"completed",status:"completed",text:`[mock] ${agent.name}: ${p.request}`,partial:false,
        execution_target:mockHostTarget,execution_target_hash:mockHostTargetHash,execution_installation_id:null};
      mockRuns.set(rid,{run,steps:[]});
      mockRunReceipts.set(rid,{...receipt,run_status:"completed",message_id:mid,cancelled:false});
      mockAgentKeys.set(key,{signature,runId:rid});
      recordRunEvent(rid,"created",{run});
      recordRunEvent(rid,"status",{run});
      return structuredClone(p.admission_only ? {run,created:true} : mockRunReceipts.get(rid));
    }
    case "agent.cancel_subagent": {
      const rid = String((params as {run_id?:string}).run_id ?? "");
      const entry = mockRuns.get(rid);
      if (!entry?.run.metadata?.agent_execution) throw new Error("Unknown durable public agent run");
      return {ok:true,cancelled:false,cancel_requested:false,run_id:rid,run_status:entry.run.status};
    }

    case "mobile.list":
      return { devices: [] };

    case "mobile.pair_start":
      return {
        token: "mock_token_abc123",
        expires_at: Date.now() + 600_000,
        qr_payload: "minimax-code://pair?token=mock_token_abc123",
      };

    case "mobile.unpair":
      return { ok: true, device_id: (params as { device_id: string }).device_id };

    case "mobile.push_notification":
      return { ok: true, broadcast: true, total_devices: 0, delivered: 0 };

    case "mobile.device_status":
      return { devices: [] };

    case "permission.list": {
      // R18 — mirror the sidecar's factory default (exec_* → ask).
      // A user rule for the same pattern shadows it, exactly like
      // the real PermissionStore.
      const covered = new Set(mockPermissionRules.map((r) => r.tool_pattern));
      const factory = covered.has("exec_*")
        ? []
        : [
            {
              id: "pr_default_exec",
              tool_pattern: "exec_*",
              action: "ask" as const,
              scope: "global",
              created_at: "",
              origin: "default" as const,
            },
          ];
      return { rules: [...mockPermissionRules, ...factory] };
    }

    case "permission.set": {
      // Wire shape (what the Python handler consumes) — the typed
      // layer translates frontend {pattern, decision} before we
      // ever see it here.
      const p = params as {
        tool_pattern: string;
        action: "allow" | "deny" | "ask";
        scope?: string;
      };
      const existing = mockPermissionRules.find(
        (r) => r.tool_pattern === p.tool_pattern,
      );
      const rule = {
        id: existing?.id ?? `pr_${Math.random().toString(36).slice(2, 10)}`,
        tool_pattern: p.tool_pattern,
        action: p.action,
        scope: p.scope ?? "global",
        created_at: existing?.created_at ?? new Date().toISOString(),
      };
      const idx = mockPermissionRules.indexOf(existing!);
      if (idx === -1) mockPermissionRules.push(rule);
      else mockPermissionRules[idx] = rule;
      return { rule };
    }

    case "permission.resolve": {
      // Mock backend: simply echo ok=true so the UI can close the
      // modal in tests. The real sidecar has the actual gater that
      // unblocks the agent loop.
      const p = params as { request_id: string; decision: "allow" | "deny" };
      return {
        ok: true,
        request_id: p.request_id,
        decision: p.decision,
      };
    }

    case "secrets.status": {
      // The mock has no env-var lookup — it can only see its own
      // in-process "keyring" store.
      return {
        configured: mockSecrets.keyring !== null,
        source: mockSecrets.keyring !== null ? "keyring" : "none",
      };
    }

    case "secrets.set": {
      const v = (params as { value?: string } | undefined)?.value ?? "";
      const trimmed = v.trim();
      if (!trimmed) {
        // Mirror the real handler's INVALID_PARAMS so the UI's
        // error toast path is exercised in tests.
        throw new Error("invalid params: 'value' must be a non-empty string");
      }
      mockSecrets.keyring = trimmed;
      return { configured: true, source: "keyring" };
    }

    case "secrets.clear": {
      mockSecrets.keyring = null;
      return { configured: false, source: "none" };
    }

    case "git.status": {
      // Mock backend has no real git binary. Return a clean
      // main branch so the top-bar widget shows a sensible
      // "no changes" state when the agent isn't reachable. The
      // unit tests for ``useGitStore`` override ``gitStatus``
      // via the typedIPC mock, so this fallback only fires in
      // browser-only mode.
      return {
        branch: "main",
        clean: true,
        ahead: 0,
        behind: 0,
        modified: [],
        untracked: [],
        staged: [],
      } satisfies GitStatusResult;
    }

    case "git.diff": {
      const p = params as { scope?: string; ref?: string; session_id?: string; run_id?: string } | undefined;
      if (p?.run_id) throw new Error("演示模式无法读取任务工作区差异");
      // A non-empty sample diff keeps the review pipeline exercisable in
      // mock mode — a constant "" here masked the whole code-review flow
      // behind the "no changes" early return.
      const sampleDiff = [
        "diff --git a/src/example.ts b/src/example.ts",
        "index 0000001..0000002 100644",
        "--- a/src/example.ts",
        "+++ b/src/example.ts",
        "@@ -1,3 +1,4 @@",
        " export function greet(name: string): string {",
        "-  return `hello ${name}`;",
        "+  // greeting with logging",
        "+  console.log(\"greeting\", name);",
        "+  return `hello ${name}`;",
        " }",
      ].join("\n");
      return { diff: sampleDiff, scope: p?.ref ?? p?.scope ?? "working", ...(p?.session_id ? {session_id:p.session_id,workspace:mockSessions.get(p.session_id)?.workspace_path ?? "/mock/workspace"} : {}) } satisfies GitDiffResult;
    }

    case "data.export": {
      // Clone so callers mutating the result can't corrupt the stored
      // envelope (matches the real handler's fresh dump per call).
      return structuredClone(mockDataEnvelope ?? MOCK_DEFAULT_ENVELOPE);
    }

    case "data.import": {
      const envelope = (params as { envelope?: DataExportEnvelope } | undefined)?.envelope;
      if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) {
        throw new Error("invalid params: 'envelope' must be an object");
      }
      if (envelope.format !== "minimax-code-export") {
        // Mirror the real handler's INVALID_PARAMS so the Data tab's
        // error path is exercised in tests.
        throw new Error(
          "invalid params: envelope.format must be 'minimax-code-export'",
        );
      }
      mockDataEnvelope = envelope;
      const imported: Record<string, number> = {};
      for (const [table, rows] of Object.entries(envelope.tables ?? {})) {
        imported[table] = Array.isArray(rows) ? rows.length : 0;
      }
      return { imported, skipped_tables: [] } satisfies DataImportSummary;
    }

    case "data.backup_list":
      return { directory: "mock://backups", items: [], truncated: false };

    case "data.backup": {
      const now = new Date();
      const stamp = now.toISOString().replace(/[:.]/g, "-");
      return {
        path: `mock://backups/minimax-code-backup-${stamp}.db`,
        bytes: 4096,
      } satisfies DataBackupResult;
    }

    case "diag.export": {
      // Mirrors the real handler's sanitized shape: enums/counts only in
      // config, basename paths, NO_DB-style degradation, short tail.
      return {
        format: "minimax-code-diagnostic",
        generated_at: new Date().toISOString(),
        version: "0.0.0-mock",
        platform: {
          system: "MockOS",
          release: "0",
          machine: "x86_64",
          python: "3.12.0",
          pid: 0,
        },
        runtime: { uptime_s: 42 },
        config: {
          log_level: "INFO",
          env: "development",
          http_host: "127.0.0.1",
          http_port: 8765,
          cors_custom_origin_count: 0,
          log_file_configured: false,
          data_dir_name: "MiniMaxCode",
          skills_dir_custom: false,
        },
        storage: {
          db_available: true,
          table_count: 2,
          tables: { sessions: 2, messages: 3 },
          migrations_applied: 1,
        },
        log_tail: ["mock agent started", "mock mode active"],
      } satisfies DiagnosticBundle;
    }

    case "patch.preview": {
      const p = params as { scope?: string; ref?: string; session_id?: string } | undefined;
      return {
        diff: "",
        scope: p?.ref ?? p?.scope ?? "working",
        ref: p?.ref ?? null,
        files: [],
        stats: { files: 0, additions: 0, deletions: 0 },
      } satisfies PatchPreviewResult;
    }

    case "patch.apply_hunk": {
      const p = params as PatchHunkOperationParams;
      return {
        ok: true,
        operation: "apply_hunk",
        scope: p.scope ?? "working",
        file_path: p.file_path,
        hunk_index: p.hunk_index,
      } satisfies PatchHunkOperationResult;
    }

    case "patch.revert_hunk": {
      const p = params as PatchHunkOperationParams;
      return {
        ok: true,
        operation: "revert_hunk",
        scope: p.scope ?? "working",
        file_path: p.file_path,
        hunk_index: p.hunk_index,
      } satisfies PatchHunkOperationResult;
    }

    case "patch.apply_file": {
      const p = params as PatchFileOperationParams;
      return {
        ok: true,
        operation: "apply_file",
        scope: p.scope ?? "working",
        file_path: p.file_path,
      } satisfies PatchFileOperationResult;
    }

    case "patch.revert_file": {
      const p = params as PatchFileOperationParams;
      return {
        ok: true,
        operation: "revert_file",
        scope: p.scope ?? "working",
        file_path: p.file_path,
      } satisfies PatchFileOperationResult;
    }

    case "patch.apply_all": {
      const p = params as { scope?: string } | undefined;
      return {
        ok: true,
        operation: "apply_all",
        scope: p?.scope ?? "working",
        applied: [],
        failed: [],
      } satisfies PatchApplyAllResult;
    }

    case "patch.revert_all": {
      const p = params as { scope?: string } | undefined;
      return {
        ok: true,
        operation: "revert_all",
        scope: p?.scope ?? "working",
        applied: [],
        failed: [],
      } satisfies PatchApplyAllResult;
    }

    case "patch.save_snapshot": {
      return { ok: true, snapshot_ref: null, clean: true } satisfies PatchSaveSnapshotResult;
    }

    case "terminal.start": {
      const p = params as { command: string; cwd?: string; session_id?: string | null };
      const session = makeMockTerminalSession({
        command: p.command,
        cwd: p.cwd ?? "",
        session_id: p.session_id ?? null,
      });
      return { session } satisfies TerminalStartResult;
    }

    case "runner.list": {
      return { runners: mockRunners } satisfies RunnerListResult;
    }

    case "runner.start": {
      const p = params as {
        runner_id: RunnerInfo["id"];
        command: string;
        cwd?: string;
        session_id?: string | null;
        sandbox_mode?: RunnerSandboxMode;
        approval_policy?: RunnerApprovalPolicy;
        permission_mode?: RunnerPermissionMode;
      };
      const runner = mockRunners.find((item) => item.id === p.runner_id);
      if (!runner) throw new Error(`unknown runner_id: ${p.runner_id}`);
      if (!runner.available) throw new Error(`${runner.label} is not runnable`);
      const session = makeMockTerminalSession({
        command:
          runner.id === "native"
            ? p.command
            : `${runner.id}: ${p.command} (${[
                p.sandbox_mode,
                p.approval_policy,
                p.permission_mode,
              ]
                .filter(Boolean)
                .join(", ")})`,
        cwd: p.cwd,
        session_id: p.session_id,
        output: `$ ${p.command}\n(mock runner output)\n`,
      });
      return { runner, session } satisfies RunnerStartResult;
    }

    case "terminal.read": {
      const p = params as { session_id: string; after_seq?: number };
      const session = mockTerminalSessions.get(p.session_id);
      if (!session) throw new Error(`unknown terminal session: ${p.session_id}`);
      const after = p.after_seq ?? 0;
      return {
        session,
        chunks: (mockTerminalChunks.get(p.session_id) ?? []).filter((chunk) => chunk.seq > after),
      } satisfies TerminalReadResult;
    }

    case "terminal.stop": {
      const p = params as { session_id: string };
      const session = mockTerminalSessions.get(p.session_id);
      if (!session) throw new Error(`unknown terminal session: ${p.session_id}`);
      const now = Date.now() / 1000;
      session.status = "cancelled";
      session.error = "stopped by user";
      session.updated_at = now;
      session.completed_at = now;
      return { session } satisfies TerminalStartResult;
    }

    case "terminal.list": {
      return {
        sessions: Array.from(mockTerminalSessions.values()).sort(
          (a, b) => b.started_at - a.started_at,
        ),
      } satisfies TerminalListResult;
    }

    case "git.log": {
      return { entries: [] } satisfies GitLogResult;
    }
    case "crash.previous_report": {
      return { available: false, report_text: null } satisfies CrashPreviousReportResult;
    }
    case "crash.history": {
      return { entries: [] } satisfies CrashHistoryResult;
    }
    case "crash.dismiss": {
      return { dismissed: false } satisfies CrashDismissResult;
    }

    // ── provider.* mock ──────────────────────────────────────────────

    case "provider.list": {
      return {
        providers: mockProviders,
      } satisfies ListProvidersResult;
    }

    case "provider.create": {
      const p = params as {
        name: string;
        protocol: "anthropic" | "openai";
        base_url: string;
        models?: { id: string; name: string; context_window: number; supports_tools: boolean; is_default?: boolean }[];
        enabled?: boolean;
      };
      const provider: ProviderInfo = {
        id: `prov_${Math.random().toString(36).slice(2, 10)}`,
        name: p.name,
        protocol: p.protocol,
        base_url: p.base_url,
        api_key_configured: false,
        models: (p.models ?? []).map((m) => ({
          id: m.id,
          name: m.name,
          context_window: m.context_window,
          supports_tools: m.supports_tools,
          is_default: m.is_default,
        })),
        enabled: p.enabled ?? true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      mockProviders.push(provider);
      return { provider } satisfies CreateProviderResult;
    }

    case "provider.update": {
      const p = params as {
        provider_id: string;
        name?: string;
        protocol?: "anthropic" | "openai";
        base_url?: string;
        models?: { id: string; name: string; context_window: number; supports_tools: boolean; is_default?: boolean }[];
        enabled?: boolean;
      };
      const prov = mockProviders.find((x) => x.id === p.provider_id);
      if (!prov) return { provider: null as unknown as ProviderInfo };
      if (p.name !== undefined) prov.name = p.name;
      if (p.protocol !== undefined) prov.protocol = p.protocol;
      if (p.base_url !== undefined) prov.base_url = p.base_url;
      if (p.models !== undefined) prov.models = p.models;
      if (p.enabled !== undefined) prov.enabled = p.enabled;
      prov.updated_at = new Date().toISOString();
      return { provider: prov } satisfies UpdateProviderResult;
    }

    case "provider.delete": {
      const p = params as { provider_id: string };
      const idx = mockProviders.findIndex((x) => x.id === p.provider_id);
      if (idx >= 0) mockProviders.splice(idx, 1);
      return { ok: true, deleted: p.provider_id } satisfies DeleteProviderResult;
    }

    case "provider.set_api_key": {
      const p = params as { provider_id: string; api_key: string };
      const prov = mockProviders.find((x) => x.id === p.provider_id);
      if (prov) {
        prov.api_key_configured = true;
        prov.updated_at = new Date().toISOString();
      }
      return {
        ok: true,
        provider_id: p.provider_id,
        api_key_configured: true,
      } satisfies SetProviderApiKeyResult;
    }

    case "provider.clear_api_key": {
      const p = params as { provider_id: string };
      const prov = mockProviders.find((x) => x.id === p.provider_id);
      if (prov) {
        prov.api_key_configured = false;
        prov.updated_at = new Date().toISOString();
      }
      return {
        ok: true,
        provider_id: p.provider_id,
        api_key_configured: false,
      } satisfies SetProviderApiKeyResult;
    }

    // ── mcp.* mock ──────────────────────────────────────────────

    case "mcp.list_servers": {
      return {
        servers: Array.from(mockMcpServers.values()),
      } satisfies ListMcpServersResult;
    }

    case "mcp.add_server": {
      const p = params as {
        id: string;
        name: string;
        transport?: McpTransport;
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
      };
      if (p.transport !== undefined && !["stdio", "sse", "streamable_http"].includes(p.transport))
        throw new Error("Unknown MCP transport");
      const now = new Date().toISOString();
      const server: McpServer = {
        id: p.id,
        name: p.name,
        transport: p.transport ?? "stdio",
        command: p.command ?? null,
        url: p.url ?? null,
        env: p.env ?? null,
        enabled: p.enabled ?? true,
        connected: false,
        bearer_token: p.bearer_token ?? null,
        headers: p.headers ?? null,
        oauth_client_id: p.oauth_client_id ?? null,
        oauth_client_secret: p.oauth_client_secret ?? null,
        oauth_scopes: p.oauth_scopes ?? null,
        oauth_callback_port: p.oauth_callback_port ?? null,
        tool_states: p.tool_states ?? null,
        created_at: now,
        updated_at: now,
      };
      mockMcpServers.set(p.id, server);
      return { server } satisfies McpServerResult;
    }

    case "mcp.update_server": {
      const p = params as {
        server_id: string;
        name?: string;
        transport?: McpTransport;
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
      };
      const s = mockMcpServers.get(p.server_id);
      if (!s) return { server: null as unknown as McpServer };
      if (p.transport !== undefined && !["stdio", "sse", "streamable_http"].includes(p.transport))
        throw new Error("Unknown MCP transport");
      if (p.name !== undefined) s.name = p.name;
      if (p.transport !== undefined) s.transport = p.transport;
      if (p.command !== undefined) s.command = p.command;
      if (p.url !== undefined) s.url = p.url;
      if (p.env !== undefined) s.env = p.env;
      if (p.enabled !== undefined) s.enabled = p.enabled;
      if (p.bearer_token !== undefined) s.bearer_token = p.bearer_token;
      if (p.headers !== undefined) s.headers = p.headers;
      if (p.oauth_client_id !== undefined) s.oauth_client_id = p.oauth_client_id;
      if (p.oauth_client_secret !== undefined) s.oauth_client_secret = p.oauth_client_secret;
      if (p.oauth_scopes !== undefined) s.oauth_scopes = p.oauth_scopes;
      if (p.oauth_callback_port !== undefined) s.oauth_callback_port = p.oauth_callback_port;
      if (p.tool_states !== undefined) s.tool_states = p.tool_states;
      s.updated_at = new Date().toISOString();
      return { server: s } satisfies McpServerResult;
    }

    case "mcp.remove_server": {
      const p = params as { server_id: string };
      const existed = mockMcpServers.delete(p.server_id);
      return { ok: existed, server_id: p.server_id } satisfies RemoveMcpServerResult;
    }

    case "mcp.list_tools": {
      const p = params as { server_name: string };
      return {
        server_name: p.server_name,
        tools: [
          { name: "read_file", description: "Read a file", inputSchema: { type: "object" } },
          { name: "list_directory", description: "List a directory", inputSchema: { type: "object" } },
        ],
      } satisfies ListMcpToolsResult;
    }

    case "mcp.invoke_tool": {
      const p = params as { server_name: string; tool_name: string; arguments?: Record<string, unknown> };
      const server = Array.from(mockMcpServers.values()).find((entry) => entry.name === p.server_name);
      if (!server || !server.enabled || !server.connected) throw new Error("MCP server is not connected or enabled");
      if (server.tool_states?.[p.tool_name] === false) throw new Error("MCP tool is disabled");
      if (!["read_file", "list_directory"].includes(p.tool_name)) throw new Error("MCP tool is not registered for this connection");
      return {
        ok: true,
        server_name: p.server_name,
        tool_name: p.tool_name,
        text: `Mock result from ${p.tool_name}`,
        content: null,
        contentBlocks: [{ type: "text", text: `Mock result from ${p.tool_name}` }],
        isError: false,
      } satisfies InvokeMcpToolResult;
    }

    // ── codebase.* mock ───────────────────────────────────────────

    case "codebase.status":
    case "codebase.build_index": {
      return {
        status: "done",
        processed: 12,
        total: 12,
        percent: 100,
        message: "Mock index ready",
        error: null,
        stats: { total_chunks: 12, total_files: 12, latest_updated_at: new Date().toISOString() },
      } satisfies CodebaseStatusResult;
    }

    case "codebase.search": {
      const p = params as { query: string; file_pattern?: string; limit?: number; offset?: number };
      return {
        query: p.query,
        file_pattern: p.file_pattern ?? null,
        total: 1,
        results: [
          {
            chunk_id: "mock-chunk-1",
            file_path: "src/example.ts",
            start_line: 1,
            end_line: 10,
            snippet: `Mock search hit for "${p.query}"`,
            language: "ts",
            rank: 1.0,
            symbols: [{ name: "example", kind: "function", line: 1, children: [] }],
          },
        ],
      } satisfies CodebaseSearchResultShape;
    }

    case "codebase.summarize": {
      const p = params as { path: string };
      return {
        path: p.path,
        kind: "file",
        language: "ts",
        total_lines: 42,
        symbols: [{ name: "example", kind: "function", line: 1, children: [] }],
        snippet: "Mock summary snippet.",
        file_count: 1,
      } satisfies CodebaseSummarizeResult;
    }

    default:
      return { ok: true };

    // ── audit.* mock ──────────────────────────────────────────────

    case "audit.list":
      return { entries: [], total: 0 } satisfies ListAuditResult;

    case "audit.stats":
      return { total: 0, by_tool: {}, by_status: {} } satisfies AuditStats;

    case "audit.purge":
      return { deleted: 0 };

    // ── telemetry.* mock (R11) — bus is disabled in mock mode ────────

    case "telemetry.recent":
      return { events: [], total: 0, enabled: false, buffered: 0 } satisfies TelemetryRecentResult;

    case "telemetry.metrics":
      return { enabled: false } satisfies TelemetryMetrics;

    case "telemetry.clear":
      return { ok: true, cleared: 0, enabled: false };

    case "telemetry.trace":
      return { trace_id: null, spans: [], tree: [], span_count: 0, enabled: false };

    // ── runtime.* mock (R12) — clean start, nothing recovered ──────────
    case "runtime.service_identity":
      return { service_id: "mock" };
    case "runtime.workspace_check":
      throw new Error("演示模式无法检查真实会话目录");
    case "runtime.research_get":
      return { saved_url: mockResearchUrl, effective_url: mockResearchUrl, source: mockResearchUrl ? "settings" : "unconfigured" };
    case "runtime.research_set": {
      const request = params as { search_url: string | null; provider?: string; brave_key?: string; clear_brave_key?: boolean };
      if (request.brave_key || request.clear_brave_key || request.provider === "brave") throw new Error("演示模式不能保存搜索密钥或启用 Brave");
      const value = request.search_url;
      if (value !== null) {
        let parsed: URL;
        try { parsed = new URL(value); } catch { throw new Error("Invalid search endpoint"); }
        if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash || !/^\/search\/?$/.test(parsed.pathname) || parsed.hostname === "localhost" || parsed.hostname.endsWith(".local") || parsed.hostname === "127.0.0.1") throw new Error("Invalid search endpoint");
      }
      mockResearchUrl = value;
      return { saved_url: value, effective_url: value, source: value ? "settings" : "unconfigured" };
    }
    case "runtime.recovery_status":
      return {
        available: true,
        clean_start: true,
        previous_crash: false,
        recovered_runs: 0,
        run_ids: [],
        sessions: [],
      } satisfies RuntimeRecoveryResult;

    // ── webhook.* mock ──────────────────────────────────────────────

    case "webhook.list":
      return { entries: [], total: 0 } satisfies ListWebhooksResult;

    case "webhook.create":
      return {
        id: "wh_mock_" + Math.random().toString(36).slice(2, 8),
        name: (params as Record<string, unknown>).name as string,
        source: ((params as Record<string, unknown>).source as WebhookConfig["source"]) || "custom",
        url_path: "/hooks/wh_mock",
        secret: null,
        enabled: true,
        action_type: ((params as Record<string, unknown>).action_type as WebhookConfig["action_type"]) || "send-message",
        action_config: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } satisfies WebhookConfig;

    case "webhook.update":
      return {
        id: (params as Record<string, unknown>).id as string,
        name: "updated",
        source: "custom",
        url_path: "/hooks/wh_mock",
        secret: null,
        enabled: true,
        action_type: "send-message",
        action_config: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } satisfies WebhookConfig;

    case "webhook.delete":
      return { deleted: true };

    case "webhook.regenerate_secret":
      return {
        id: (params as Record<string, unknown>).id as string,
        name: "mock",
        source: "custom",
        url_path: "/hooks/wh_mock",
        secret: "new_mock_secret_" + Math.random().toString(36).slice(2),
        enabled: true,
        action_type: "send-message",
        action_config: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } satisfies WebhookConfig;

    // ── notification.* mock ──────────────────────────────────────────

    case "notification.list":
      return { entries: [], total: 0 } satisfies ListNotificationsResult;

    case "notification.mark_read": {
      const p = params as { id: string };
      return {
        id: p.id,
        type: "info",
        title: "mock",
        body: "",
        priority: 0,
        read: true,
        created_at: new Date().toISOString(),
      } satisfies NotificationEntry;
    }

    case "notification.mark_all_read":
      return { marked: 0 };

    case "notification.delete":
      return { deleted: true };

    case "notification.purge":
      return { purged: 0 };

    // ── workflow.* mock ──────────────────────────────────────────

    case "workflow.list":
      return { entries: [], total: 0 } satisfies ListWorkflowsResult;

    case "workflow.create": {
      const p = params as { name: string; trigger_type: string; description?: string };
      const now = new Date().toISOString();
      return {
        id: `wf_mock${Date.now().toString(36)}`,
        name: p.name,
        description: p.description ?? "",
        enabled: true,
        trigger_type: p.trigger_type as WorkflowEntry["trigger_type"],
        trigger_config: {},
        steps: [],
        last_run_at: null,
        run_count: 0,
        created_at: now,
        updated_at: now,
      } satisfies WorkflowEntry;
    }

    case "workflow.update": {
      const p = params as { id: string; name?: string };
      return {
        id: p.id,
        name: p.name ?? "updated",
        description: "",
        enabled: true,
        trigger_type: "webhook" as const,
        trigger_config: {},
        steps: [],
        last_run_at: null,
        run_count: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } satisfies WorkflowEntry;
    }

    case "workflow.delete":
      return { deleted: true };

    case "workflow.enable":
    case "workflow.disable": {
      const p = params as { id: string };
      return {
        id: p.id,
        name: "mock",
        description: "",
        enabled: method === "workflow.enable",
        trigger_type: "webhook" as const,
        trigger_config: {},
        steps: [],
        last_run_at: null,
        run_count: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } satisfies WorkflowEntry;
    }

    case "workflow.trigger":
      if (input?.skill_list) return { chains: [], next_offset: null };
      if (input?.skill_status || input?.skill_execution) throw new Error("Mock mode does not support durable skill workflows");
      return { ok: true, steps_completed: 0, steps_failed: 0 };

    // ── team.* mock (v0.8.0) ──────────────────────────────────────────

    case "team.list":
      return { teams: mockTeams } satisfies ListTeamsResult;

    case "team.get": {
      const p = params as { name: string };
      const t = mockTeams.find((x) => x.name === p.name);
      if (!t) return { team: null as unknown as AgentTeam };
      return { team: t };
    }

    case "team.create": {
      const p = params as {
        name: string;
        description?: string;
        icon?: string;
        color?: string;
        agents?: string[];
        orchestration_mode?: string;
      };
      const now = new Date().toISOString();
      const team: AgentTeam = {
        id: `team_mock_${Math.random().toString(36).slice(2, 8)}`,
        name: p.name,
        description: p.description ?? "",
        icon: p.icon ?? "",
        color: p.color ?? "",
        agents: p.agents ?? [],
        orchestration_mode: (p.orchestration_mode as OrchestrationMode) ?? "parallel",
        enabled: true,
        created_at: now,
        updated_at: now,
      };
      mockTeams.push(team);
      return { team } satisfies { team: AgentTeam };
    }

    case "team.update": {
      const p = params as {
        name: string;
        description?: string;
        icon?: string;
        color?: string;
        agents?: string[];
        orchestration_mode?: string;
      };
      const t = mockTeams.find((x) => x.name === p.name);
      if (!t) return { team: null as unknown as AgentTeam };
      if (p.description !== undefined) t.description = p.description;
      if (p.icon !== undefined) t.icon = p.icon;
      if (p.color !== undefined) t.color = p.color;
      if (p.agents !== undefined) t.agents = p.agents;
      if (p.orchestration_mode !== undefined) t.orchestration_mode = p.orchestration_mode as OrchestrationMode;
      t.updated_at = new Date().toISOString();
      return { team: t } satisfies { team: AgentTeam };
    }

    case "team.delete": {
      const p = params as { name: string };
      const idx = mockTeams.findIndex((x) => x.name === p.name);
      if (idx >= 0) mockTeams.splice(idx, 1);
      return { ok: true, name: p.name };
    }

    case "team.enable":
    case "team.disable": {
      const p = params as { name: string };
      const t = mockTeams.find((x) => x.name === p.name);
      if (!t) return { team: null as unknown as AgentTeam };
      t.enabled = method === "team.enable";
      t.updated_at = new Date().toISOString();
      return { team: t } satisfies { team: AgentTeam };
    }
    case "team.spawn": {
      const p = params as { team_name: string; request: string; session_id?: string; parent_session_id?: string; mode?: string; budget_tokens?: number; idempotency_key?: string; max_parallel?: number };
      if (p.mode !== undefined && p.mode !== "execute") throw new Error("Team execution is unavailable in planning mode");
      if (p.max_parallel !== undefined && (!Number.isInteger(p.max_parallel) || p.max_parallel < 1 || p.max_parallel > 16)) throw new Error("max_parallel must be an integer from 1 to 16");
      const sessionId = p.session_id || p.parent_session_id;
      if (!sessionId || !mockSessions.has(sessionId) || (p.parent_session_id && !mockSessions.has(p.parent_session_id))) {
        throw new Error("team.spawn requires an existing session");
      }
      const key = p.idempotency_key ? `${sessionId}:${p.idempotency_key}` : null;
      const signature = JSON.stringify([p.team_name, p.request, p.parent_session_id ?? null, p.budget_tokens ?? null, p.max_parallel ?? null]);
      const previous = key ? mockTeamRequests.get(key) : undefined;
      if (previous) {
        if (previous.signature !== signature) throw new Error("idempotency_key already belongs to different input");
        return structuredClone(previous.result);
      }
      const result = {
        team_name: p.team_name,
        orchestration_mode: "parallel",
        merged_text: `[mock] Team ${p.team_name} processed: ${p.request}`,
        agents_run: [
          { agent_name: "agent-1", success: true, text: `Handled: ${p.request}`, error: "", iterations: 1, stub: true },
        ],
        conflicts: [],
        task_id: `teamrun_mock_${crypto.randomUUID()}`,
        success: true,
        budget: null,
        max_parallel: p.max_parallel ?? 2,
      };
      if (key) mockTeamRequests.set(key, { signature, result });
      return result;
    }

    // ── task.* mock (v0.11.0) ────────────────────────────────────────

    case "task.list": {
      const p = params as { session_id?: string; status?: string; limit?: number } | undefined;
      const limit = p?.limit ?? 100;
      const entries: TaskRow[] = [
        {
          id: "task_mock_index",
          session_id: "mock-ses-1",
          title: "Mock index build",
          status: "completed",
          progress: 100,
          created_at: new Date(Date.now() - 60_000).toISOString(),
          started_at: new Date(Date.now() - 55_000).toISOString(),
          completed_at: new Date(Date.now() - 10_000).toISOString(),
          error: null,
          result: "Indexed 42 files · 128 chunks · FTS + vector shards rebuilt",
          run_id: null, cancel_supported: false,
          verification: { version: 1, verdict: "unknown", reason: "legacy_unverified", requires_review: false },
        },
      ];
      let items = entries;
      if (p?.session_id) items = items.filter((t) => t.session_id === p.session_id);
      if (p?.status) items = items.filter((t) => t.status === p.status);
      return { tasks: items.slice(0, limit) } satisfies TaskListResult;
    }

    case "task.cancel": {
      throw new Error("Mock task has no cancellable run; cancellation is unavailable");
    }

    // ── checkpoint.* mock (v0.11.0) ──────────────────────────────────

    case "checkpoint.list": {
      const p = params as { session_id: string } | undefined;
      const items = Array.from(mockCheckpoints.values())
        .filter((c) => !p?.session_id || c.session_id === p.session_id)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      return { checkpoints: items, count: items.length } satisfies CheckpointListResult;
    }

    case "checkpoint.create": {
      const p = params as { session_id: string; label?: string; message?: string };
      const now = new Date().toISOString();
      const checkpoint: Checkpoint = {
        id: `ckpt_${Math.random().toString(36).slice(2, 10)}`,
        session_id: p.session_id,
        label: p.label || "Checkpoint",
        message: p.message || "",
        git_stash_ref: `refs/stash@{${Math.floor(Math.random() * 100)}}`,
        branch: "main",
        tracked_files: ["src/example.ts"],
        untracked_files: [],
        has_untracked_snapshot: false,
        source_root: "/mock/workspace",
        created_at: now,
      };
      mockCheckpoints.set(checkpoint.id, checkpoint);
      return { checkpoint } satisfies CheckpointCreateResult;
    }

    case "checkpoint.restore": {
      const p = params as { checkpoint_id: string; untracked_file?: string };
      const checkpoint = mockCheckpoints.get(p.checkpoint_id);
      if (!checkpoint) throw new Error(`unknown checkpoint: ${p.checkpoint_id}`);
      if (p.untracked_file !== undefined) throw new Error("模拟模式无法恢复实际文件");
      return {
        result: {
          checkpoint_id: checkpoint.id,
          restored: true,
          applied_stash: true,
          restored_untracked: [],
          skipped_existing: [],
          warnings: [],
        },
      } satisfies CheckpointRestoreResult;
    }

    case "checkpoint.rebind": {
      const p = params as { checkpoint_id: string; cwd?: string; confirm_rebind?: boolean; confirm_trusted_bundle?: boolean };
      const checkpoint = mockCheckpoints.get(p.checkpoint_id);
      if (!checkpoint || !p.cwd || p.confirm_rebind !== true || p.confirm_trusted_bundle !== true) {
        throw new Error("checkpoint rebind requires a target and explicit confirmations");
      }
      throw new Error("模拟模式无法验证 Git 对象，不能重新绑定检查点");
    }

    case "checkpoint.diff": {
      const p = params as { checkpoint_id: string };
      const checkpoint = mockCheckpoints.get(p.checkpoint_id);
      if (!checkpoint) throw new Error(`unknown checkpoint: ${p.checkpoint_id}`);
      return {
        checkpoint_id: checkpoint.id,
        available: true,
        patch: `diff --git a/src/example.ts b/src/example.ts\n@@ -1,1 +1,2 @@\n old\n+new`,
        files: ["src/example.ts"],
        untracked_preview: checkpoint.untracked_files.map((path) => ({ path, status: "missing_snapshot" as const })),
      } satisfies CheckpointDiffResult;
    }

    case "checkpoint.delete": {
      const p = params as { checkpoint_id: string };
      const existed = mockCheckpoints.delete(p.checkpoint_id);
      return { ok: true, removed_snapshot: existed } satisfies CheckpointDeleteResult;
    }

    // ── memory.* mock (v0.11.0) ─────────────────────────────────────

    case "memory.list": {
      const p = params as {
        project_id?: string;
        session_id?: string;
        category?: MemoryCategory;
        limit?: number;
        offset?: number;
      } | undefined;
      let items = Array.from(mockMemories.values()).sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      if (p?.project_id) items = items.filter((m) => m.project_id === p.project_id);
      if (p?.session_id) items = items.filter((m) => m.session_id === p.session_id);
      if (p?.category) items = items.filter((m) => m.category === p.category);
      const offset = p?.offset ?? 0;
      const limit = p?.limit ?? items.length;
      return {
        memories: items.slice(offset, offset + limit),
        total: items.length,
      } satisfies ListMemoriesResult;
    }

    case "memory.search": {
      const p = params as {
        query: string;
        project_id?: string;
        category?: MemoryCategory;
        limit?: number;
      };
      const q = p.query.trim().toLowerCase();
      let items = Array.from(mockMemories.values()).filter((m) =>
        m.content.toLowerCase().includes(q),
      );
      if (p.project_id) items = items.filter((m) => m.project_id === p.project_id);
      if (p.category) items = items.filter((m) => m.category === p.category);
      items.sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      const limit = p.limit ?? items.length;
      return { memories: items.slice(0, limit), total: items.length } satisfies ListMemoriesResult;
    }

    case "memory.add": {
      const p = params as {
        content: string;
        category?: MemoryCategory;
        confidence?: number;
        project_id?: string;
        session_id?: string;
        source?: string;
      };
      const now = new Date().toISOString();
      const memory: MemoryEntry = {
        id: `mem_${Math.random().toString(36).slice(2, 10)}`,
        project_id: p.project_id ?? null,
        session_id: p.session_id ?? null,
        content: p.content.trim(),
        category: p.category ?? "fact",
        confidence: p.confidence ?? 1.0,
        source: p.source ?? null,
        created_at: now,
        updated_at: now,
      };
      mockMemories.set(memory.id, memory);
      return { memory } satisfies MemoryAddResult;
    }

    case "memory.delete": {
      const p = params as { id: string };
      const existed = mockMemories.delete(p.id);
      return { ok: existed, id: p.id } satisfies MemoryDeleteResult;
    }

    case "memory.extract": {
      const p = params as { text: string };
      const sentences = p.text
        .replace(/([.!?])\s+/g, "$1\n")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      return {
        facts: sentences.map((content) => ({
          content,
          category: "fact" as const,
          confidence: 0.8,
        })),
      } satisfies MemoryExtractResult;
    }

    // ── plugins.* mock (platform pillar #3) ───────────────────────────

    case "plugins.list": {
      const p = (params ?? {}) as { include_failed?: boolean; enabled_only?: boolean };
      let items = mockPlugins.slice();
      if (p.enabled_only) items = items.filter((x) => x.enabled);
      if (p.include_failed === false) items = items.filter((x) => x.ok);
      return { plugins: items, total: items.length } satisfies ListPluginsResult;
    }

    case "plugins.info": {
      const p = params as { name: string };
      const plugin = mockPlugins.find((x) => x.name === p.name);
      if (!plugin) return { plugin: null as unknown as PluginInfo };
      return { plugin } satisfies PluginInfoResult;
    }

    case "plugins.enable":
    case "plugins.disable": {
      const p = params as { name: string };
      const plugin = mockPlugins.find((x) => x.name === p.name);
      const enabled = method === "plugins.enable";
      if (plugin) plugin.enabled = enabled;
      return { ok: true, name: p.name, enabled } satisfies PluginToggleResult;
    }

    case "plugins.reload": {
      const items = mockPlugins.slice();
      return {
        ok: true,
        total: items.length,
        reloaded: items.filter((x) => x.ok).length,
        failed: items.filter((x) => !x.ok).length,
        plugins: items,
      } satisfies PluginReloadResult;
    }
  }
}
