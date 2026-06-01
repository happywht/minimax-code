/**
 * IPC client — wraps Tauri `invoke` and `event` so the rest of the
 * frontend never has to know the wire format.
 *
 * Features:
 *  - Per-id response promise map (concurrent requests don't interfere).
 *  - Strongly-typed `request<T>(method, params)` for ergonomic RPC.
 *  - Stream subscriptions via `on(event, cb)` returning an unlisten.
 *  - Browser / unit-test fallback: if running outside Tauri, methods
 *    go through a tiny in-process mock so the UI shell is shippable
 *    without the Python sidecar.
 *  - Toast-able IPCError with code + data for ErrorBoundary display.
 *
 * See `docs/ipc-contract.md` for the protocol.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  ErrorCode,
  StreamEvent,
  type CreateSessionResult,
  type JsonRpcError,
  type JsonRpcEvent,
  type JsonRpcId,
  type JsonRpcRequest,
  type JsonRpcResponse,
  type ListAgentsResult,
  type ListJobsResult,
  type ListMessagesResult,
  type ListModelsResult,
  type ListRulesResult,
  type ListSessionsResult,
  type ListSkillsResult,
  type MessageChunkData,
  type Message as ProtocolMessage,
  type PermissionRequestData,
  type PermissionResolvedData,
  type PermissionRule,
  type SendMessageResult,
  type Session,
  type SetModelResult,
  type SetRuleResult,
  type SidecarEvent,
  type SkillInfo,
  type ScheduledJob,
  type AgentInfo,
  type ModelInfo,
  type SpawnSubagentResult,
  type TaskProgressData,
  type ToolCallData,
  type ToolResultData,
} from "../types/ipc";

const RESPONSE_EVENT = "ipc:response";
const EVENT_NAME = "ipc:event";
const SIDE_CAR_EVENT = "ipc:sidecar";

type Pending = {
  resolve: (value: unknown) => void;
  reject: (err: JsonRpcError) => void;
};

type EventListener = (payload: JsonRpcEvent) => void;
type ResponseListener = (payload: JsonRpcResponse) => void;
type SideCarListener = (payload: SidecarEvent) => void;

/** Public IPC error class — also exported for ErrorBoundary. */
export class IPCError extends Error {
  code: number;
  data: unknown;
  constructor(err: JsonRpcError) {
    super(
      `[${err.code}] ${err.message}` +
        (err.data != null ? `: ${JSON.stringify(err.data)}` : ""),
    );
    this.name = "IPCError";
    this.code = err.code;
    this.data = err.data;
  }
}

export type StreamEventPayload =
  | { event: typeof StreamEvent.MessageChunk; data: MessageChunkData }
  | { event: typeof StreamEvent.AgentStatus; data: unknown }
  | { event: typeof StreamEvent.ToolCall; data: ToolCallData }
  | { event: typeof StreamEvent.ToolResult; data: ToolResultData }
  | { event: typeof StreamEvent.PermissionRequest; data: PermissionRequestData }
  | { event: typeof StreamEvent.PermissionResolved; data: PermissionResolvedData }
  | { event: typeof StreamEvent.TaskProgress; data: TaskProgressData };

/** Returns true when the runtime is inside a Tauri webview. */
export function isTauri(): boolean {
  return (
    typeof window !== "undefined" &&
    "__TAURI_INTERNALS__" in (window as unknown as Record<string, unknown>)
  );
}

export class IPCClient {
  private pending = new Map<JsonRpcId, Pending>();
  private listeners = new Map<string, Set<EventListener>>();
  private responseListeners = new Set<ResponseListener>();
  private sideCarListeners = new Set<SideCarListener>();
  private unlistenResponse: UnlistenFn | null = null;
  private unlistenEvent: UnlistenFn | null = null;
  private unlistenSideCar: UnlistenFn | null = null;
  private started = false;
  private readonly useMock: boolean;

  constructor(opts?: { forceMock?: boolean }) {
    this.useMock = !!opts?.forceMock || !isTauri();
  }

  /** True when this client is running in mock mode (no Tauri shell). */
  get isMock(): boolean {
    return this.useMock;
  }

  /** Subscribe to Tauri events exactly once. Idempotent. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    if (this.useMock) {
      this.started = true;
      return;
    }
    this.unlistenResponse = await listen<JsonRpcResponse>(RESPONSE_EVENT, (e) => {
      const resp = e.payload;
      this.responseListeners.forEach((cb) => cb(resp));
      const pending = this.pending.get(resp.id);
      if (!pending) return;
      this.pending.delete(resp.id);
      if (resp.error) {
        pending.reject(resp.error);
      } else {
        pending.resolve(resp.result);
      }
    });

    this.unlistenEvent = await listen<JsonRpcEvent>(EVENT_NAME, (e) => {
      const evt = e.payload;
      const set = this.listeners.get(evt.event);
      if (set) {
        set.forEach((cb) => cb(evt));
      }
    });

    this.unlistenSideCar = await listen<SidecarEvent>(SIDE_CAR_EVENT, (e) => {
      this.sideCarListeners.forEach((cb) => cb(e.payload));
    });
  }

  async stop(): Promise<void> {
    this.unlistenResponse?.();
    this.unlistenResponse = null;
    this.unlistenEvent?.();
    this.unlistenEvent = null;
    this.unlistenSideCar?.();
    this.unlistenSideCar = null;
    this.started = false;
  }

  /**
   * Send a JSON-RPC request and await its response. The Rust sidecar
   * assigns a uuid if we don't pass one.
   */
  async request<T = unknown>(
    method: string,
    params?: unknown,
    id?: JsonRpcId,
  ): Promise<T> {
    const requestId = id ?? this.uuid();
    // We deliberately build the full envelope here for parity with the
    // wire format (also makes debugging easier if we ever log it).
    const envelope: JsonRpcRequest = {
      jsonrpc: "2.0",
      id: requestId,
      method,
      params,
    };
    void envelope; // currently unused — invoke sends the fields directly

    if (this.useMock) {
      return mockRequest<T>(method, params, this);
    }
    void this;

    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    await invoke("ipc_request", {
      method,
      params: params ?? null,
      id: id ?? null,
    });
    try {
      return (await promise) as T;
    } catch (err) {
      throw new IPCError(err as JsonRpcError);
    }
  }

  /** Fire-and-forget notification (no id, no response expected). */
  async notify(method: string, params?: unknown): Promise<void> {
    if (this.useMock) {
      mockNotify(method, params, this);
      return;
    }
    await invoke("ipc_notify", {
      method,
      params: params ?? null,
    });
  }

  /** Subscribe to a push event (e.g. `agent.message_chunk`). */
  on<T = unknown>(
    event: string,
    cb: (payload: { event: string; data?: T }) => void,
  ): () => void {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }
    const wrapped: EventListener = (env) => {
      cb({ event: env.event, data: env.data as T | undefined });
    };
    set.add(wrapped);
    return () => {
      set?.delete(wrapped);
    };
  }

  /** Subscribe to a strongly-typed stream event. */
  onStreamEvent<K extends (typeof StreamEvent)[keyof typeof StreamEvent]>(
    event: K,
    cb: (data: Extract<StreamEventPayload, { event: K }>["data"]) => void,
  ): () => void {
    return this.on(event, (env) => {
      if (env.data !== undefined) cb(env.data as never);
    });
  }

  onSideCar(cb: SideCarListener): () => void {
    this.sideCarListeners.add(cb);
    return () => {
      this.sideCarListeners.delete(cb);
    };
  }

  /** Liveness probe — true once the Rust bridge has started. */
  async ping(): Promise<boolean> {
    if (this.useMock) return true;
    try {
      return (await invoke("ping_agent")) as boolean;
    } catch {
      return false;
    }
  }

  /** Mock-mode event emitter (for browser / tests). */
  _emit(event: string, data: unknown): void {
    const set = this.listeners.get(event);
    if (!set) return;
    for (const cb of set) {
      cb({ jsonrpc: "2.0", event, data });
    }
  }

  /** Mock-mode response resolver (used by mockRequest / mockNotify). */
  _resolveResponse(id: JsonRpcId, result: unknown): void {
    const pending = this.pending.get(id);
    if (!pending) return;
    this.pending.delete(id);
    pending.resolve(result);
  }

  /** Mock-mode error resolver. */
  _rejectResponse(id: JsonRpcId, err: JsonRpcError): void {
    const pending = this.pending.get(id);
    if (!pending) return;
    this.pending.delete(id);
    pending.reject(err);
  }

  /** Mock-mode sidecar emitter. */
  _emitSideCar(payload: SidecarEvent): void {
    this.sideCarListeners.forEach((cb) => cb(payload));
  }

  private uuid(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      return crypto.randomUUID();
    }
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}

/* ─────────────────────── Typed high-level API ─────────────────────── */

export interface TypedIPC {
  ping(): Promise<boolean>;

  // session
  listSessions(opts?: { archived?: boolean; limit?: number; offset?: number }): Promise<ListSessionsResult>;
  createSession(opts?: { title?: string; model_id?: string }): Promise<CreateSessionResult>;
  archiveSession(sessionId: string): Promise<{ ok: true }>;
  unarchiveSession(sessionId: string): Promise<{ ok: true }>;
  deleteSession(sessionId: string): Promise<{ ok: true }>;
  listMessages(sessionId: string, opts?: { limit?: number; before?: string }): Promise<ListMessagesResult>;

  // agent
  sendMessage(opts: { session_id: string | null; content: string; attachments?: unknown }): Promise<SendMessageResult>;
  cancelAgent(sessionId: string): Promise<{ ok: true }>;

  // model
  listModels(): Promise<ListModelsResult>;
  setCurrentModel(modelId: string): Promise<SetModelResult>;

  // skill
  listSkills(): Promise<ListSkillsResult>;
  enableSkill(skillId: string): Promise<{ ok: true }>;
  disableSkill(skillId: string): Promise<{ ok: true }>;
  invokeSkill(skillId: string, args: unknown): Promise<{ ok: true; output: unknown }>;

  // scheduler
  listJobs(): Promise<ListJobsResult>;
  createJob(opts: { name: string; cron: string; prompt: string }): Promise<{ job: ScheduledJob }>;
  deleteJob(jobId: string): Promise<{ ok: true }>;
  toggleJob(jobId: string, enabled: boolean): Promise<{ ok: true }>;

  // agent (multi-agent)
  listAgents(): Promise<ListAgentsResult>;
  spawnSubagent(opts: { agent_id: string; prompt: string }): Promise<SpawnSubagentResult>;

  // mobile
  pairDevice(opts: { code: string }): Promise<{ device_id: string }>;
  listDevices(): Promise<{ devices: { id: string; name: string; paired_at: number }[] }>;
  sendToDevice(deviceId: string, payload: unknown): Promise<{ ok: true }>;

  // permission
  listRules(): Promise<ListRulesResult>;
  setRule(rule: Omit<PermissionRule, "id" | "created_at"> & { id?: string }): Promise<SetRuleResult>;
  deleteRule(ruleId: string): Promise<{ ok: true }>;
}

export function bindTypedIPC(client: IPCClient): TypedIPC {
  return {
    ping: () => client.ping(),

    listSessions: (opts) =>
      client.request<ListSessionsResult>("session.list", opts ?? {}),
    createSession: (opts) =>
      client.request<CreateSessionResult>("session.create", opts ?? {}),
    archiveSession: (sid) =>
      client.request<{ ok: true }>("session.archive", { session_id: sid }),
    unarchiveSession: (sid) =>
      client.request<{ ok: true }>("session.unarchive", { session_id: sid }),
    deleteSession: (sid) =>
      client.request<{ ok: true }>("session.delete", { session_id: sid }),
    listMessages: (sid, opts) =>
      client.request<ListMessagesResult>("message.list", {
        session_id: sid,
        ...(opts ?? {}),
      }),

    sendMessage: (opts) =>
      client.request<SendMessageResult>("agent.send_message", opts),
    cancelAgent: (sid) =>
      client.request<{ ok: true }>("agent.cancel", { session_id: sid }),

    listModels: () => client.request<ListModelsResult>("model.list", {}),
    setCurrentModel: (modelId) =>
      client.request<SetModelResult>("model.set_current", { model_id: modelId }),

    listSkills: () => client.request<ListSkillsResult>("skill.list", {}),
    enableSkill: (sid) =>
      client.request<{ ok: true }>("skill.enable", { skill_id: sid }),
    disableSkill: (sid) =>
      client.request<{ ok: true }>("skill.disable", { skill_id: sid }),
    invokeSkill: (sid, args) =>
      client.request<{ ok: true; output: unknown }>("skill.invoke", {
        skill_id: sid,
        args,
      }),

    listJobs: () => client.request<ListJobsResult>("scheduler.list_jobs", {}),
    createJob: (opts) =>
      client.request<{ job: ScheduledJob }>("scheduler.create_job", opts),
    deleteJob: (jid) =>
      client.request<{ ok: true }>("scheduler.delete_job", { job_id: jid }),
    toggleJob: (jid, enabled) =>
      client.request<{ ok: true }>("scheduler.toggle_job", {
        job_id: jid,
        enabled,
      }),

    listAgents: () => client.request<ListAgentsResult>("agent.list_agents", {}),
    spawnSubagent: (opts) =>
      client.request<SpawnSubagentResult>("agent.spawn_subagent", opts),

    pairDevice: (opts) =>
      client.request<{ device_id: string }>("mobile.pair", opts),
    listDevices: () =>
      client.request<{
        devices: { id: string; name: string; paired_at: number }[];
      }>("mobile.list_devices", {}),
    sendToDevice: (did, payload) =>
      client.request<{ ok: true }>("mobile.send", {
        device_id: did,
        payload,
      }),

    listRules: () => client.request<ListRulesResult>("permission.list_rules", {}),
    setRule: (rule) =>
      client.request<SetRuleResult>("permission.set_rule", rule),
    deleteRule: (rid) =>
      client.request<{ ok: true }>("permission.delete_rule", { rule_id: rid }),
  };
}

/* ─────────────────────────── Mock backend ─────────────────────────── */

/**
 * The mock backend is intentionally minimal — it just lets the UI shell
 * render in a plain browser (or under vitest) without the Tauri Rust
 * shell. Real answers come from the Python agent.
 */
function mockRequest<T>(
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

function mockNotify(method: string, params: unknown, client: IPCClient): void {
  mockHandle(method, params, client, null);
}

const mockSessions = new Map<string, Session>();
const mockModels: ModelInfo[] = [
  {
    id: "minimax-M2.7",
    name: "M2.7",
    provider: "MiniMax",
    context_window: 200000,
    supports_tools: true,
    is_default: true,
  },
  {
    id: "minimax-M2.7-fast",
    name: "M2.7 Fast",
    provider: "MiniMax",
    context_window: 128000,
    supports_tools: true,
  },
  {
    id: "minimax-M2.7-pro",
    name: "M2.7 Pro",
    provider: "MiniMax",
    context_window: 1000000,
    supports_tools: true,
  },
];
const mockSkills: SkillInfo[] = [
  {
    id: "commit-helper",
    name: "Commit Helper",
    description: "Draft commit messages from staged diffs",
    enabled: true,
    builtin: true,
  },
  {
    id: "code-review",
    name: "Code Review",
    description: "Review a diff for bugs and style issues",
    enabled: true,
    builtin: true,
  },
];
const mockAgents: AgentInfo[] = [
  {
    id: "general",
    name: "General",
    description: "General-purpose sub-agent",
    enabled: true,
  },
  {
    id: "researcher",
    name: "Researcher",
    description: "Web research and summarization",
    enabled: true,
  },
];
const mockJobs: ScheduledJob[] = [];

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
      const sid = `ses_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const session: Session = {
        id: sid,
        title:
          (params as { title?: string } | undefined)?.title ?? "New task",
        archived: false,
        created_at: now,
        updated_at: now,
        model_id: null,
      };
      mockSessions.set(sid, session);
      return { session_id: sid };
    }

    case "session.list": {
      return {
        sessions: Array.from(mockSessions.values()).sort(
          (a, b) => b.updated_at - a.updated_at,
        ),
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

    case "message.list": {
      return { messages: [] as ProtocolMessage[] };
    }

    case "agent.send_message": {
      const p = params as { session_id: string | null; content: string };
      const sid = p.session_id ?? `ses_${Math.random().toString(36).slice(2, 10)}`;
      const mid = `msg_${Math.random().toString(36).slice(2, 10)}`;
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
        }, 12 * i);
      });
      return { session_id: sid, message_id: mid, text: "" };
    }

    case "agent.cancel":
    case "skill.enable":
    case "skill.disable":
    case "scheduler.delete_job":
    case "scheduler.toggle_job":
    case "permission.delete_rule":
    case "mobile.send":
      return { ok: true };

    case "model.list":
      return { models: mockModels, current: mockModels[0].id };

    case "model.set_current":
      return {
        current:
          (params as { model_id: string }).model_id ?? mockModels[0].id,
      };

    case "skill.list":
      return { skills: mockSkills };

    case "skill.invoke":
      return { ok: true, output: { skill: (params as { skill_id: string }).skill_id } };

    case "scheduler.list_jobs":
      return { jobs: mockJobs };

    case "scheduler.create_job": {
      const p = params as { name: string; cron: string; prompt: string };
      const job: ScheduledJob = {
        id: `job_${Math.random().toString(36).slice(2, 10)}`,
        name: p.name,
        cron: p.cron,
        prompt: p.prompt,
        enabled: true,
        last_run_at: null,
        next_run_at: null,
      };
      mockJobs.push(job);
      return { job };
    }

    case "agent.list_agents":
      return { agents: mockAgents };

    case "agent.spawn_subagent":
      return {
        agent_run_id: `run_${Math.random().toString(36).slice(2, 10)}`,
        agent_id: (params as { agent_id: string }).agent_id,
      };

    case "mobile.list_devices":
      return { devices: [] };

    case "mobile.pair":
      return { device_id: `dev_${Math.random().toString(36).slice(2, 10)}` };

    case "permission.list_rules":
      return { rules: [] };

    case "permission.set_rule": {
      const p = params as Omit<PermissionRule, "id" | "created_at"> & {
        id?: string;
      };
      const rule: PermissionRule = {
        id: p.id ?? `rule_${Math.random().toString(36).slice(2, 10)}`,
        tool: p.tool,
        pattern: p.pattern,
        decision: p.decision,
        created_at: Date.now(),
      };
      return { rule };
    }

    default:
      return { ok: true };
  }
}

/* ─────────────────────── Module-level singleton ─────────────────────── */

/** Singleton for the app — Tauri or mock depending on the runtime. */
export const ipc = new IPCClient();

/** Typed binding on top of the singleton. */
export const typedIPC: TypedIPC = bindTypedIPC(ipc);

export { ErrorCode };
