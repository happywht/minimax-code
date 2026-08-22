/**
 * Mock backend — in-process JSON-RPC handler that lets the UI shell
 * render in a plain browser (or under vitest) without the Python agent.
 * Real answers come from the Python agent over HTTP + WebSocket.
 *
 * Split out of `client.ts` during the ipc-module restructure; the
 * `mockHandle` switch and its helpers are preserved verbatim.
 */

import { StreamEvent } from "../types/ipc";
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
  SubAgentProgress,
  TelemetryMetrics,
  TelemetryRecentResult,
  TerminalListResult,
  TerminalReadResult,
  TerminalStartResult,
  UpdateProviderResult,
  WebhookConfig,
  WorkflowEntry,
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
  TaskCancelResult,
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
  return new Promise<T>((resolve) => {
    // Resolve on the next microtask so the caller's `await` always
    // gets a turn to settle (avoids the "test timed out" issue when
    // the caller is also driving setTimeout / fake timers).
    Promise.resolve().then(() => {
      const result = mockHandle(method, params, client, id);
      resolve(result as T);
    });
  });
}

export function mockNotify(method: string, params: unknown, client: IPCClient): void {
  mockHandle(method, params, client, null);
}
// R63: mock-side echo of the persisted reasoning-effort override. Mirrors the
// R61 backend write-side read-back — the frontend badge switcher consumes it
// from `model.list` so it stays in sync with the store without a second round-trip.
let mockReasoningEffort: string | null = null;
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
function mockHandle(
  method: string,
  params: unknown,
  client: IPCClient,
  id: JsonRpcId | null,
): unknown {
  switch (method) {
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

    case "session.create": {
      const p = params as {
        title?: string;
        reuse_empty_session_id?: string;
        project_id?: string;
        workspace_mode?: "local" | "worktree";
        workspace_path?: string;
        worktree_branch?: string;
        base_branch?: string;
      } | undefined;
      const reusable = p?.reuse_empty_session_id
        ? mockSessions.get(p.reuse_empty_session_id)
        : undefined;
      if (
        reusable
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
      const p = params as { title?: string; base_ref?: string } | undefined;
      const session: Session = {
        id: sid,
        title: p?.title ?? DEFAULT_WORKTREE_TITLE,
        archived: false,
        created_at: now,
        updated_at: now,
        model_id: null,
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

    case "project.list": {
      const p = params as { archived?: boolean } | undefined;
      const projects = Array.from(mockProjects.values())
        .filter((proj) => (p?.archived === undefined ? true : proj.archived === Boolean(p.archived)))
        .sort((a, b) => b.updated_at - a.updated_at);
      return { projects };
    }

    case "project.create": {
      const p = params as { name: string; description?: string };
      const pid = `proj_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const project = {
        id: pid,
        name: p.name,
        description: p.description ?? "",
        archived: false,
        created_at: now,
        updated_at: now,
      };
      mockProjects.set(pid, project);
      return { project };
    }

    case "project.update": {
      const p = params as { project_id: string; name?: string; description?: string };
      const project = mockProjects.get(p.project_id);
      if (!project) return { ok: false, project: null };
      if (p.name !== undefined) project.name = p.name;
      if (p.description !== undefined) project.description = p.description;
      project.updated_at = Date.now();
      return { ok: true, project };
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
      return { messages: [] as ProtocolMessage[] };
    }

    case "message.update": {
      const p = params as { message_id: string; content?: string };
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
      return { ok: true, message_id: p.message_id } satisfies DeleteMessageResult;
    }

    case "run.list": {
      const p = params as { session_id?: string; limit?: number; offset?: number } | undefined;
      let runs = Array.from(mockRuns.values()).map((r) => r.run);
      if (p?.session_id) runs = runs.filter((r) => r.session_id === p.session_id);
      runs.sort((a, b) => b.created_at.localeCompare(a.created_at));
      const offset = p?.offset ?? 0;
      const limit = p?.limit ?? 20;
      return { runs: runs.slice(offset, offset + limit) } satisfies ListRunsResult;
    }

    case "run.steps": {
      const runId = (params as { run_id: string }).run_id;
      const entry = mockRuns.get(runId);
      return {
        run: entry?.run ?? null,
        steps: entry?.steps ?? [],
      };
    }

    case "agent.send_message": {
      const p = params as { session_id: string | null; content: string };
      const sid = p.session_id ?? `ses_${Math.random().toString(36).slice(2, 10)}`;
      mockSessionsWithMessages.add(sid);
      const mid = `msg_${Math.random().toString(36).slice(2, 10)}`;
      const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
      const nowIso = new Date().toISOString();
      const run: import("../types/ipc").AgentRun = {
        id: runId,
        session_id: sid,
        mode: "chat",
        status: "running",
        title: String(p.content).slice(0, 32),
        assistant_message_id: mid,
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
      setTimeout(() => client._emit(StreamEvent.RunCreated, { run }), 0);
      setTimeout(() => client._emit(StreamEvent.RunStepStarted, { run_id: runId, step: thinkingStep }), 8);
      const reply = `(mock reply) Received: ${p.content}`;
      const chunks = reply.match(/.{1,12}/g) ?? [reply];
      chunks.forEach((delta, i) => {
        setTimeout(() => {
          client._emit(StreamEvent.MessageChunk, {
            session_id: sid,
            message_id: mid,
            delta,
            done: i === chunks.length - 1,
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
              metadata: { iterations: 1, memory_count: 0 },
            };
            mockRuns.set(runId, { run: completedRun, steps: [completedStep, finalStep] });
            client._emit(StreamEvent.RunStepCompleted, { run_id: runId, step: completedStep });
            client._emit(StreamEvent.RunStepCompleted, { run_id: runId, step: finalStep });
            client._emit(StreamEvent.RunCompleted, { run: completedRun });
          }
        }, 12 * i);
      });
      return { session_id: sid, message_id: mid, run_id: runId, text: "" };
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
      // v1.1.0 — resume a budget-truncated turn. The real backend
      // validates the latest run then re-enters send_message with a
      // fixed prompt; the mock just replays the send path.
      const p = params as { session_id: string };
      return mockHandle(
        "agent.send_message",
        { session_id: p.session_id, content: "[continue] (mock) resuming the truncated turn…" },
        client,
        id,
      );
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
      const p = params as { name: string; system_prompt: string };
      const a: AgentInfo = { id: `agent_${Date.now()}`, name: p.name, description: p.system_prompt.slice(0, 40), enabled: true, system_prompt: p.system_prompt };
      mockAgents.push(a);
      return { agent: a };
    }
    case "agent.update": {
      const p = params as { name: string };
      const a = mockAgents.find((x) => x.name === p.name);
      if (a && "enabled" in p) a.enabled = p.enabled as boolean;
      return { agent: a ?? null };
    }

    case "model.list":
      return {
        models: mockModels,
        current: mockModels[0].id,
        reasoning_effort: mockReasoningEffort,
      };

    case "model.set_current":
      return {
        current:
          (params as { model_id: string }).model_id ?? mockModels[0].id,
      };

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

    case "skill.invoke":
      return { ok: true, output: { skill: (params as { skill_id: string }).skill_id } };

    case "schedule.list":
      return { jobs: mockJobs };

    case "schedule.create": {
      // The wire speaks ``cron_expr`` + ``payload``; we accept either
      // (caller may use either) and normalise to the ScheduledJob
      // shape the UI consumes (``cron`` + ``prompt``).
      const p = params as
        | { name: string; cron_expr?: string; cron?: string; payload?: { prompt?: string }; prompt?: string };
      const cron = p.cron_expr ?? p.cron ?? "";
      const prompt = p.prompt ?? p.payload?.prompt ?? "";
      const job: ScheduledJob = {
        id: `job_${Math.random().toString(36).slice(2, 10)}`,
        name: p.name,
        cron,
        prompt,
        enabled: true,
        last_run_at: null,
        next_run_at: null,
      };
      mockJobs.push(job);
      return { job };
    }

    case "schedule.enable":
    case "schedule.disable": {
      const p = params as { job_id: string };
      const job = mockJobs.find((j) => j.id === p.job_id);
      if (job) job.enabled = method === "schedule.enable";
      return { job: job ?? null };
    }
    case "schedule.run_now": {
      const p = params as { job_id: string };
      return { ok: true as const, job_id: p.job_id, triggered_at: Date.now() / 1000 };
    }

    case "agent.list":
      return { agents: mockAgents };

    case "agent.spawn_subagent": {
      // The mock backend fabricates a stream of ``agent.subagent_progress``
      // events that mirror the real backend's v0.3.0 shape (see
      // ``docs/v0.3.0-design.md`` §2). This lets the UI exercise the
      // full progress / completion lifecycle in offline mode.
      //
      // NOTE: ``bindTypedIPC.spawnSubagent`` maps the frontend-facing
      // ``SpawnSubagentParams`` fields (``agent_id``, ``prompt``) to the
      // backend wire format (``name``, ``request``). The mock handler
      // sees the mapped payload, so we read from ``name``/``request``.
      const p = params as Record<string, unknown>;
      const agentName = (p.name as string) || "general";
      const requestText = (p.request as string) || "";
      // Prefer client-supplied run_id; fall back to server-generated.
      const runId = (p.run_id as string) || `run_${Math.random().toString(36).slice(2, 10)}`;
      const basePayload = {
        run_id: runId,
        agent_id: agentName,
        parent_session_id: p.parent_session_id as string | undefined,
        context_message_id: p.context_message_id as string | undefined,
        received_at: Date.now(),
      };
      const stages: Array<{ status: SubAgentProgress["status"]; progress: number; summary: string; text?: string }> = [
        { status: "started", progress: 0.05, summary: `starting ${agentName}` },
        { status: "thinking", progress: 0.25, summary: `thinking about: ${requestText.slice(0, 40)}` },
        { status: "tool_call", progress: 0.55, summary: "calling read_file" },
        { status: "tool_result", progress: 0.7, summary: "got 2 lines" },
        { status: "completed", progress: 1.0, summary: "done", text: `(mock sub-agent reply) ${requestText}` },
      ];
      stages.forEach((stage, i) => {
        setTimeout(() => {
          client._emit(StreamEvent.SubAgentProgress, {
            ...basePayload,
            ...stage,
            received_at: Date.now(),
          } satisfies SubAgentProgress);
        }, 80 * (i + 1));
      });
      return { agent_run_id: runId, agent_id: agentName };
    }

    case "agent.cancel_subagent":
      return { ok: true, cancelled: false };

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
      const p = params as { scope?: string; ref?: string } | undefined;
      return { diff: "", scope: p?.ref ?? p?.scope ?? "working" } satisfies GitDiffResult;
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
      const p = params as { scope?: string; ref?: string } | undefined;
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
      };
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
      };
      const s = mockMcpServers.get(p.server_id);
      if (!s) return { server: null as unknown as McpServer };
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
      return {
        ok: true,
        server_name: p.server_name,
        tool_name: p.tool_name,
        text: `Mock result from ${p.tool_name}`,
        content: null,
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
      const p = params as { team_name: string; request: string };
      return {
        team_name: p.team_name,
        orchestration_mode: "parallel",
        merged_text: `[mock] Team ${p.team_name} processed: ${p.request}`,
        agents_run: [
          { agent_name: "agent-1", success: true, text: `Handled: ${p.request}`, error: "", iterations: 1, stub: true },
        ],
        conflicts: [],
        task_id: `teamrun_mock_${Date.now()}`,
        success: true,
      };
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
        },
      ];
      let items = entries;
      if (p?.session_id) items = items.filter((t) => t.session_id === p.session_id);
      if (p?.status) items = items.filter((t) => t.status === p.status);
      return { tasks: items.slice(0, limit) } satisfies TaskListResult;
    }

    case "task.cancel": {
      const p = params as { task_id: string };
      return {
        ok: true,
        task: {
          id: p.task_id,
          session_id: "mock-ses-1",
          title: "Mock cancelled task",
          status: "cancelled",
          progress: 0,
          created_at: new Date().toISOString(),
          started_at: null,
          completed_at: new Date().toISOString(),
          error: null,
        },
        noop: false,
      } satisfies TaskCancelResult;
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
        created_at: now,
      };
      mockCheckpoints.set(checkpoint.id, checkpoint);
      return { checkpoint } satisfies CheckpointCreateResult;
    }

    case "checkpoint.restore": {
      const p = params as { checkpoint_id: string };
      const checkpoint = mockCheckpoints.get(p.checkpoint_id);
      if (!checkpoint) throw new Error(`unknown checkpoint: ${p.checkpoint_id}`);
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

    case "checkpoint.diff": {
      const p = params as { checkpoint_id: string };
      const checkpoint = mockCheckpoints.get(p.checkpoint_id);
      if (!checkpoint) throw new Error(`unknown checkpoint: ${p.checkpoint_id}`);
      return {
        checkpoint_id: checkpoint.id,
        available: true,
        patch: `diff --git a/src/example.ts b/src/example.ts\n@@ -1,1 +1,2 @@\n old\n+new`,
        files: ["src/example.ts"],
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
