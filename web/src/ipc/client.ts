/**
 * IPC client — fetch + WebSocket transport to the Python agent.
 *
 * Replaces the v0.1.x Tauri `invoke` / `listen` bridge. The wire
 * format is the same JSON-RPC 2.0 envelope the agent already speaks;
 * only the transport changes.
 *
 *   POST /rpc   — JSON-RPC request → response (one-shot)
 *   GET  /ws    — server-push events (agent.message_chunk, …)
 *   GET  /health— liveness probe used by `ping()` and `start()`.
 *
 * Mode selection:
 *   - `VITE_AGENT_MODE === "mock"` (or `mockMode: true` in opts)
 *     forces the in-process mock backend. Useful for tests and
 *     browser-only UI work.
 *   - Otherwise `start()` probes `GET /health`. A 2xx flips to
 *     `mode = "http"`. A failure (network error, 5xx, timeout)
 *     falls back to mock so the UI keeps working offline.
 *
 * The mock backend (the big `mockHandle` switch) is preserved
 * verbatim so existing UI plumbing and Vitest snapshots keep
 * working without changes.
 *
 * See `docs/v0.2.0-web-architecture.md` for the protocol details.
 */

import {
  ErrorCode,
  StreamEvent,
  type AgentInfo,
  type CreateProviderResult,
  type CreateSessionResult,
  type DeleteProviderResult,
  type GitDiffResult,
  type GitLogResult,
  type GitStatusResult,
  type JsonRpcError,
  type JsonRpcEvent,
  type JsonRpcId,
  type JsonRpcRequest,
  type JsonRpcResponse,
  type ListAgentsResult,
  type ListJobsResult,
  type ListMessagesResult,
  type ListModelsResult,
  type ListProvidersResult,
  type ListRulesResult,
  type ListSessionsResult,
  type ListSkillsResult,
  type MessageChunkData,
  type Message as ProtocolMessage,
  type ModelInfo,
  type PermissionRequestData,
  type PermissionResolvedData,
  type PermissionRule,
  type ProviderInfo,
  type ScheduledJob,
  type SecretStatus,
  type SendMessageResult,
  type Session,
  type SetModelResult,
  type SetProviderApiKeyResult,
  type SetRuleResult,
  type SidecarEvent,
  type SkillInfo,
  type SpawnSubagentParams,
  type SpawnSubagentResult,
  type SubAgentProgress,
  type TaskProgressData,
  type ToolCallData,
  type ToolResultData,
  type UpdateProviderResult,
} from "../types/ipc";

/* ─────────────────────── Internal types ─────────────────────── */

type ClientMode = "http" | "mock";

type Pending = {
  resolve: (value: unknown) => void;
  reject: (err: JsonRpcError) => void;
};

type EventListener = (payload: JsonRpcEvent) => void;
type ResponseListener = (payload: JsonRpcResponse) => void;
type SideCarListener = (payload: SidecarEvent) => void;

/* ─────────────────────── Env helpers ─────────────────────── */

/**
 * Read a Vite-injected env var without dragging in `vite/client`
 * (we don't currently include that types file). Falls back to
 * `undefined` outside a Vite build (Vitest, raw node).
 */
function readViteEnv(key: string): string | undefined {
  try {
    const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> })
      .env;
    if (env && typeof env[key] === "string") {
      return env[key] as string;
    }
  } catch {
    // import.meta.env unavailable (e.g. raw node test runner) — skip
  }
  return undefined;
}

const DEFAULT_AGENT_BASE_URL = "http://127.0.0.1:8765";

/** Convert an `http://host:port` base URL to its `ws://` equivalent. */
function toWebSocketUrl(baseUrl: string, path: string): string {
  const wsBase = baseUrl.replace(/^http/i, "ws").replace(/\/+$/, "");
  return `${wsBase}${path}`;
}

/* ─────────────────────── Public API ─────────────────────── */

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

/**
 * @deprecated Retained as a no-op stub for backward compatibility
 * with `App.tsx` and any external callers. We never run inside a
 * Tauri webview anymore, so this always returns `false`. Use
 * `isAgentReachable(baseUrl?)` for runtime detection.
 */
export function isTauri(): boolean {
  return false;
}

/**
 * Async liveness probe — returns `true` if `GET <baseUrl>/health`
 * responds with a 2xx. Defaults to the standard agent base URL.
 * `null` / undefined / empty `baseUrl` falls through to the default.
 */
export async function isAgentReachable(baseUrl?: string): Promise<boolean> {
  const url = baseUrl && baseUrl.length > 0 ? baseUrl : DEFAULT_AGENT_BASE_URL;
  try {
    const resp = await fetch(`${url.replace(/\/+$/, "")}/health`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    return resp.ok;
  } catch {
    return false;
  }
}

export interface IPCClientOptions {
  /** Override the agent base URL. Default: `VITE_AGENT_URL` || `http://127.0.0.1:8765`. */
  baseUrl?: string;
  /** Force the in-process mock backend (bypasses the `/health` probe). */
  mockMode?: boolean;
  /** Alias for ``mockMode`` — kept for backward-compat with test callers. */
  forceMock?: boolean;
}

export class IPCClient {
  private pending = new Map<JsonRpcId, Pending>();
  private listeners = new Map<string, Set<EventListener>>();
  private responseListeners = new Set<ResponseListener>();
  private sideCarListeners = new Set<SideCarListener>();
  private ws: WebSocket | null = null;
  private wsReconnectAttempts = 0;
  private wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private wsOpen = false;
  private started = false;
  private mode: ClientMode = "http";
  private readonly baseUrl: string;
  private readonly forceMock: boolean;

  constructor(opts?: IPCClientOptions) {
    const envUrl = readViteEnv("VITE_AGENT_URL");
    this.baseUrl = (opts?.baseUrl && opts.baseUrl.length > 0
      ? opts.baseUrl
      : envUrl && envUrl.length > 0
        ? envUrl
        : DEFAULT_AGENT_BASE_URL
    ).replace(/\/+$/, "");
    this.forceMock =
      !!opts?.mockMode || !!opts?.forceMock || readViteEnv("VITE_AGENT_MODE") === "mock";
    // Pre-set the mode so the client behaves correctly even if a
    // caller invokes `request()` / `ping()` before `start()`. This
    // matches the v0.1.x contract where `useMock` was decided in
    // the constructor. `start()` will only flip the mode if the
    // constructor left it on the default "http" path AND the
    // /health probe fails.
    this.mode = this.forceMock ? "mock" : "http";
  }

  /** True when this client is running in mock mode (no real agent). */
  get isMock(): boolean {
    return this.mode === "mock";
  }

  /** True after `start()` has selected the transport. */
  get isHttp(): boolean {
    return this.mode === "http";
  }

  /** Resolved agent base URL (post env-var merge, trailing slash stripped). */
  get agentBaseUrl(): string {
    return this.baseUrl;
  }

  /**
   * Boot the transport.
   *
   *  - In forced-mock mode (constructor opt or `VITE_AGENT_MODE=mock`)
   *    skip the probe and use the in-process mock.
   *  - Otherwise `GET /health`. 2xx → `mode = "http"` and a WebSocket
   *    is opened. Failure → `mode = "mock"` and the UI keeps working.
   *
   * Idempotent. Returns once mode is decided; the WebSocket will
   * reconnect in the background if it drops.
   */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    if (this.forceMock) {
      this.mode = "mock";
      return;
    }

    const reachable = await isAgentReachable(this.baseUrl);
    if (reachable) {
      this.mode = "http";
      this.connectWs();
    } else {
      this.mode = "mock";
    }
  }

  /** Tear down transport — useful for tests. */
  async stop(): Promise<void> {
    if (this.wsReconnectTimer != null) {
      clearTimeout(this.wsReconnectTimer);
      this.wsReconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore — close is best-effort
      }
      this.ws = null;
    }
    this.wsOpen = false;
    this.rejectAllPending({
      code: ErrorCode.InternalError,
      message: "IPC client stopped",
    });
    this.started = false;
  }

  /**
   * Send a JSON-RPC request and await its response.
   *
   *  - In mock mode: dispatches to the in-process mock backend.
   *  - In HTTP mode: `POST /rpc`, returns `result`, throws `IPCError`
   *    on `error` envelope.
   */
  async request<T = unknown>(
    method: string,
    params?: unknown,
    id?: JsonRpcId,
  ): Promise<T> {
    const requestId = id ?? this.uuid();
    // Build the envelope for parity with the wire format. The HTTP
    // path re-serialises the same fields below.
    const envelope: JsonRpcRequest = {
      jsonrpc: "2.0",
      id: requestId,
      method,
      params,
    };
    void envelope;

    if (this.mode === "mock") {
      return mockRequest<T>(method, params, this);
    }

    return this.httpRequest<T>(method, params, requestId);
  }

  /** Fire-and-forget notification (no id, no response expected). */
  async notify(method: string, params?: unknown): Promise<void> {
    if (this.mode === "mock") {
      mockNotify(method, params, this);
      return;
    }
    // HTTP mode: POST without awaiting a body. Swallow network
    // errors with a console warning so a flaky network doesn't
    // ripple into the UI loop.
    void fetch(`${this.baseUrl}/rpc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        method,
        params: params ?? null,
      }),
    }).catch((err) => {
      // eslint-disable-next-line no-console
      console.warn(`[ipc] notify(${method}) failed:`, err);
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

  /** Liveness probe — `true` if the agent answered `/health` (or mock). */
  async ping(): Promise<boolean> {
    if (this.mode === "mock") return true;
    return isAgentReachable(this.baseUrl);
  }

  /* ────────── Mock-mode helpers (for tests + offline UI) ────────── */

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

  /* ────────── Private: WebSocket + dispatch ────────── */

  private connectWs(): void {
    if (this.ws) {
      // Already connecting/connected — bail.
      return;
    }
    const wsUrl = toWebSocketUrl(this.baseUrl, "/ws");
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (err) {
      // Constructing the WS can throw synchronously in some
      // environments (e.g. invalid URL). Treat as a connection
      // failure and schedule a reconnect.
      this.ws = null;
      this.scheduleReconnect();
      void err;
      return;
    }
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.wsOpen = true;
      // Note: we deliberately do NOT reset `wsReconnectAttempts` on
      // open. The task spec (and the tests in __tests__/client-http.test.ts)
      // require the backoff sequence to monotonically grow through
      // repeated failures — 250, 500, 1000, 2000, 4000, 5000, 5000...
      // Resetting would collapse every reconnect back to 250ms.
      this.dispatchSideCar({ status: "started" });
    });

    ws.addEventListener("message", (e) => {
      this.handleWsMessage(e.data);
    });

    ws.addEventListener("close", () => {
      const wasOpen = this.wsOpen;
      this.wsOpen = false;
      this.ws = null;
      if (wasOpen) {
        this.dispatchSideCar({ status: "stopped" });
      }
      // Reject in-flight pending so callers can retry.
      this.rejectAllPending({
        code: ErrorCode.InternalError,
        message: "WebSocket disconnected",
      });
      this.scheduleReconnect();
    });

    ws.addEventListener("error", () => {
      // Errors always fire `close` after — let that handler do the
      // reconnect bookkeeping. Forward as a sidecar event for UIs
      // that care.
      this.dispatchSideCar({
        status: "error",
        error: "WebSocket error",
      });
    });
  }

  private handleWsMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let env: unknown;
    try {
      env = JSON.parse(raw);
    } catch {
      return;
    }
    if (!env || typeof env !== "object") return;
    this.handleEnvelope(env as Record<string, unknown>);
  }

  /**
   * Dispatch a single JSON-RPC envelope to the right subscriber set.
   * Routing rules:
   *  - Envelopes with an `id` AND (`result` or `error`) are
   *    responses → resolve/reject `pending` + notify `responseListeners`.
   *  - Envelopes with a `method` (no `id`) are events/notifications.
   *    Special-cased:
   *      - `agent.ready` is lifecycle, no listener fan-out.
   *      - `sidecar.*` (or method === "sidecar") → `sideCarListeners`.
   *    Otherwise → `listeners.get(method)`.
   */
  private handleEnvelope(env: Record<string, unknown>): void {
    const id = env.id as JsonRpcId | undefined;
    const hasResult = "result" in env;
    const hasError = "error" in env;
    if (id !== undefined && (hasResult || hasError)) {
      const resp = env as unknown as JsonRpcResponse;
      this.responseListeners.forEach((cb) => cb(resp));
      const pending = this.pending.get(resp.id);
      if (pending) {
        this.pending.delete(resp.id);
        if (resp.error) {
          pending.reject(resp.error);
        } else {
          pending.resolve(resp.result);
        }
      }
      return;
    }

    const method = env.method;
    if (typeof method !== "string" || method.length === 0) return;

    // Lifecycle — server confirms the runtime is up. No listener
    // fan-out; presence of an open WebSocket is the signal UIs use.
    if (method === "agent.ready") return;

    // Sidecar events: method = "sidecar" or "sidecar.*". We accept
    // either bare status (top-level `params.status`) or a fully
    // shaped `SidecarEvent` in `params`.
    if (method === "sidecar" || method.startsWith("sidecar.")) {
      const params = (env.params ?? {}) as Partial<SidecarEvent>;
      if (params.status === "started" || params.status === "stopped" || params.status === "error") {
        this.dispatchSideCar({ status: params.status, error: params.error });
      } else {
        this.dispatchSideCar({ status: "started" });
      }
      return;
    }

    // Regular stream event.
    const set = this.listeners.get(method);
    if (!set || set.size === 0) return;
    const evt: JsonRpcEvent = {
      jsonrpc: "2.0",
      event: method,
      data: env.params,
    };
    for (const cb of set) {
      cb(evt);
    }
  }

  private dispatchSideCar(payload: SidecarEvent): void {
    this.sideCarListeners.forEach((cb) => cb(payload));
  }

  private scheduleReconnect(): void {
    if (!this.started || this.mode !== "http") return;
    if (this.wsReconnectTimer != null) return;
    this.wsReconnectAttempts += 1;
    // 250ms → 500ms → 1s → 2s, cap at 5s.
    const delay = Math.min(
      5000,
      250 * 2 ** Math.min(this.wsReconnectAttempts - 1, 5),
    );
    this.wsReconnectTimer = setTimeout(() => {
      this.wsReconnectTimer = null;
      this.connectWs();
    }, delay);
  }

  private rejectAllPending(err: JsonRpcError): void {
    if (this.pending.size === 0) return;
    for (const [, pending] of this.pending) {
      pending.reject(err);
    }
    this.pending.clear();
  }

  private async httpRequest<T>(
    method: string,
    params: unknown,
    id: JsonRpcId,
  ): Promise<T> {
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id,
          method,
          params: params ?? null,
        }),
      });
    } catch (err) {
      throw new IPCError({
        code: ErrorCode.InternalError,
        message: `network error: ${err instanceof Error ? err.message : String(err)}`,
      });
    }

    if (!resp.ok) {
      // 5xx / non-2xx → transport-level failure, not a JSON-RPC error.
      throw new IPCError({
        code: ErrorCode.InternalError,
        message: `HTTP ${resp.status} ${resp.statusText}`,
      });
    }

    let envelope: JsonRpcResponse;
    try {
      envelope = (await resp.json()) as JsonRpcResponse;
    } catch (err) {
      throw new IPCError({
        code: ErrorCode.ParseError,
        message: `failed to parse response: ${err instanceof Error ? err.message : String(err)}`,
      });
    }

    if (envelope.error) {
      throw new IPCError(envelope.error);
    }
    return envelope.result as T;
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
  updateSession(sessionId: string, fields: { title?: string }): Promise<UpdateSessionResult>;
  listMessages(sessionId: string, opts?: { limit?: number; before?: string }): Promise<ListMessagesResult>;

  // agent
  sendMessage(opts: { session_id: string | null; content: string; attachments?: unknown }): Promise<SendMessageResult>;
  cancelAgent(sessionId: string): Promise<{ ok: true }>;

  // agent CRUD
  getAgent(name: string): Promise<{ agent: AgentInfo }>;
  createAgent(opts: { name: string; system_prompt: string; tool_allowlist?: string[]; model?: string }): Promise<{ agent: AgentInfo }>;
  updateAgent(opts: { name: string; system_prompt?: string; tool_allowlist?: string[]; model?: string; enabled?: boolean }): Promise<{ agent: AgentInfo }>;
  deleteAgent(name: string): Promise<{ ok: true }>;

  // model
  listModels(): Promise<ListModelsResult>;
  setCurrentModel(modelId: string): Promise<SetModelResult>;

  // skill
  listSkills(): Promise<ListSkillsResult>;
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

  // mobile
  startPairing(opts?: { suggested_name?: string }): Promise<{ token: string; expires_at: number; qr_payload: string }>;
  listDevices(): Promise<{ devices: { id: string; name: string; paired_at: number }[] }>;
  unpairDevice(deviceId: string): Promise<{ ok: true; device_id: string }>;
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
    updateSession: (sid, fields) =>
      client.request<UpdateSessionResult>("session.update", {
        session_id: sid,
        ...fields,
      }),
    listMessages: (sid, opts) =>
      client.request<ListMessagesResult>("message.list", {
        session_id: sid,
        ...(opts ?? {}),
      }),

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

    listSkills: () => client.request<ListSkillsResult>("skill.list", {}),
    enableSkill: (sid) =>
      client.request<{ ok: true }>("skill.enable", { skill_id: sid }),
    disableSkill: (sid) =>
      client.request<{ ok: true }>("skill.disable", { skill_id: sid }),
    invokeSkill: (sid, args) =>
      client.request<{ ok: true; output: unknown }>("skill.invoke", {
        skill_id: sid,
        request: args,
      }),

    listJobs: () => client.request<ListJobsResult>("schedule.list", {}),
    createJob: (opts) =>
      client.request<{ job: ScheduledJob }>("schedule.create", {
        name: opts.name,
        // Wire names differ from the JS API for historical reasons.
        cron_expr: opts.cron,
        payload: { prompt: opts.prompt },
      }),
    deleteJob: (jid) =>
      client.request<{ ok: true }>("schedule.delete", { job_id: jid }),
    enableJob: (jid) =>
      client.request<{ job: ScheduledJob }>("schedule.enable", { job_id: jid }),
    disableJob: (jid) =>
      client.request<{ job: ScheduledJob }>("schedule.disable", { job_id: jid }),
    runNowJob: (jid) =>
      client.request<{ ok: true; job_id: string; triggered_at: number | null }>("schedule.run_now", { job_id: jid }),

    listAgents: () => client.request<ListAgentsResult>("agent.list", {}),
    // Map frontend keys (agent_id, prompt) → backend keys (name, request).
    // The backend handler requires `name` and `request`; the frontend
    // type uses `agent_id` and `prompt` for clarity.
    spawnSubagent: (opts) =>
      client.request<SpawnSubagentResult>("agent.spawn_subagent", {
        name: opts.agent_id,
        request: opts.prompt,
        parent_session_id: opts.parent_session_id,
        context_message_id: opts.context_message_id,
        display_name: opts.display_name,
      }),

    startPairing: (opts) =>
      client.request<{ token: string; expires_at: number; qr_payload: string }>(
        "mobile.pair_start",
        opts ?? {},
      ),
    listDevices: () =>
      client.request<{
        devices: { id: string; name: string; paired_at: number }[];
      }>("mobile.list", {}),
    unpairDevice: (did) =>
      client.request<{ ok: true; device_id: string }>("mobile.unpair", {
        device_id: did,
      }),
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
  };
}

/* ─────────────────────────── Mock backend ─────────────────────────── */

/**
 * The mock backend is intentionally minimal — it just lets the UI shell
 * render in a plain browser (or under vitest) without the Python agent.
 * Real answers come from the Python agent over HTTP + WebSocket.
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

const mockProviders: ProviderInfo[] = [
  {
    id: "builtin-minimax",
    name: "MiniMax",
    protocol: "anthropic",
    base_url: "https://api.minimaxi.com/anthropic",
    api_key_configured: false,
    models: [
      { id: "MiniMax-M3", name: "MiniMax-M3", context_window: 200000, supports_tools: true, is_default: true },
      { id: "MiniMax-M3-fast", name: "MiniMax-M3-fast", context_window: 128000, supports_tools: true },
      { id: "MiniMax-Code", name: "MiniMax-Code", context_window: 1000000, supports_tools: true },
    ],
    enabled: true,
    created_at: "2026-06-06T00:00:00Z",
    updated_at: "2026-06-06T00:00:00Z",
  },
];

/**
 * Mock secret store — pretends to be the OS keyring for browser /
 * unit-test runs. Stored values never persist across reloads (it's
 * just an in-process Map), which is fine because the mock is for
 * UI plumbing only. The real backend is the OS Credential Manager.
 */
const mockSecrets: { keyring: string | null } = { keyring: null };

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
    case "schedule.delete":
    case "permission.delete":
    case "agent.delete":
      return { ok: true };

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
      const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
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

    case "permission.list":
      return { rules: [] };

    case "permission.set": {
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

    case "git.log": {
      return { entries: [] } satisfies GitLogResult;
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

    default:
      return { ok: true };
  }
}

/* ─────────────────────── Module-level singleton ─────────────────────── */

/** Singleton for the app — http transport when the agent is up, mock otherwise. */
export const ipc = new IPCClient();

/** Typed binding on top of the singleton. */
export const typedIPC: TypedIPC = bindTypedIPC(ipc);

export { ErrorCode };
