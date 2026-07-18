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
  type AgentTeam,
  type AuditStats,
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
  type ListAuditResult,
  type ListJobsResult,
  type ListMessagesResult,
  type ListModelsResult,
  type ListProvidersResult,
  type ListRunsResult,
  type ListRulesResult,
  type ListSessionsResult,
  type ListSkillsResult,
  type ListPluginsResult,
  type ListTeamsResult,
  type PluginInfo,
  type PluginInfoResult,
  type PluginReloadResult,
  type PluginToggleResult,
  type ListWebhooksResult,
  type WebhookConfig,
  type ListNotificationsResult,
  type NotificationEntry,
  type WorkflowEntry,
  type ListWorkflowsResult,
  type MessageChunkData,
  type Message as ProtocolMessage,
  type ModelInfo,
  type OrchestrationMode,
  type PatchHunkOperationParams,
  type PatchHunkOperationResult,
  type PatchPreviewResult,
  type PermissionRequestData,
  type PermissionResolvedData,
  type RunCompletedData,
  type RunCreatedData,
  type RunnerInfo,
  type RunnerApprovalPolicy,
  type RunnerListResult,
  type RunnerPermissionMode,
  type RunnerSandboxMode,
  type RunnerStartResult,
  type RunStepsResult,
  type RunStepData,
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
  type TeamProgressData as _TeamProgressData,
  type TerminalChunk,
  type TerminalListResult,
  type TerminalReadResult,
  type TerminalSession,
  type TerminalStartResult,
  type ToolCallData,
  type ToolResultData,
  type UpdateProviderResult,
  type UpdateSessionResult,
} from "../types/ipc";

/* ─────────────────────── Internal types ─────────────────────── */

type ClientMode = "pending" | "http" | "mock";

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

function isViteDevelopment(): boolean {
  try {
    return Boolean((import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV);
  } catch {
    return false;
  }
}

const DEFAULT_AGENT_BASE_URL = "http://127.0.0.1:8765";

function runtimeAgentBaseUrl(): string {
  if (
    !isViteDevelopment() &&
    typeof window !== "undefined" &&
    /^https?:$/.test(window.location.protocol)
  ) {
    return window.location.origin;
  }
  return DEFAULT_AGENT_BASE_URL;
}

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
  | { event: typeof StreamEvent.TaskProgress; data: TaskProgressData }
  | { event: typeof StreamEvent.RunCreated; data: RunCreatedData }
  | { event: typeof StreamEvent.RunStepStarted; data: RunStepData }
  | { event: typeof StreamEvent.RunStepCompleted; data: RunStepData }
  | { event: typeof StreamEvent.RunCompleted; data: RunCompletedData };

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
  const url = baseUrl && baseUrl.length > 0 ? baseUrl : runtimeAgentBaseUrl();
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
  /** Override the agent base URL. Production defaults to the current page origin. */
  baseUrl?: string;
  /** Force the in-process mock backend (bypasses the `/health` probe). */
  mockMode?: boolean;
  /** Alias for ``mockMode`` — kept for backward-compat with test callers. */
  forceMock?: boolean;
}

/**
 * Long-running RPC methods that need a higher HTTP timeout.
 *
 * ``agent.send_message`` streams chunks via WebSocket but the HTTP POST
 * only resolves after the entire agent loop finishes (potentially
 * minutes).  Without a generous timeout the frontend aborts the request
 * at 30 s, producing a misleading ``[-32603] timed out`` error even
 * though the backend is still running and WS chunks are flowing.
 */
const LONG_RUNNING_METHODS: Record<string, number> = {
  "agent.send_message": 0,       // WS events own lifecycle; no client deadline
  "skill.invoke": 120_000,       // 2 min — skill execution
};

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
  private mode: ClientMode = "pending";
  private readonly baseUrl: string;
  private readonly forceMock: boolean;
  /** Resolved when `start()` finishes the mode-selection probe. */
  private startPromise: Promise<void> | null = null;
  private startResolve: (() => void) | null = null;

  constructor(opts?: IPCClientOptions) {
    const envUrl = readViteEnv("VITE_AGENT_URL");
    this.baseUrl = (opts?.baseUrl && opts.baseUrl.length > 0
      ? opts.baseUrl
      : envUrl && envUrl.length > 0
        ? envUrl
        : runtimeAgentBaseUrl()
    ).replace(/\/+$/, "");
    this.forceMock =
      !!opts?.mockMode || !!opts?.forceMock || readViteEnv("VITE_AGENT_MODE") === "mock";
    // Start in "pending" — no network requests until `start()` probes
    // /health and decides between "http" and "mock".  This prevents
    // the ERR_CONNECTION_REFUSED console-spam that occurs when
    // components fire useEffect IPC calls before the async `start()`
    // resolves.
    this.mode = this.forceMock ? "mock" : "pending";
    if (this.mode === "pending") {
      this.startPromise = new Promise<void>((r) => { this.startResolve = r; });
    }
  }

  /** True when this client is running in mock mode (no real agent). */
  get isMock(): boolean {
    return this.mode === "mock" || this.mode === "pending";
  }

  /** True after `start()` has selected the transport. */
  get isHttp(): boolean {
    return this.mode === "http";
  }

  /** True when mock mode was explicitly requested via options or env. */
  get isForcedMock(): boolean {
    return this.forceMock;
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
    if (this.started) {
      // Already started but mode may still be "pending" if another
      // caller invoked start() first and the probe hasn't resolved.
      if (this.startPromise) await this.startPromise;
      return;
    }
    this.started = true;

    if (this.forceMock) {
      this.mode = "mock";
      this.startResolve?.();
      return;
    }

    const reachable = await isAgentReachable(this.baseUrl);
    this.mode = reachable ? "http" : "mock";
    if (reachable) this.connectWs();
    this.startResolve?.();
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
    if (this.forceMock) {
      this.mode = "mock";
      this.startPromise = null;
      this.startResolve = null;
    } else {
      this.mode = "pending";
      this.startPromise = new Promise<void>((r) => { this.startResolve = r; });
    }
  }

  /**
   * Force a fresh transport probe.
   *
   * This is intentionally stronger than `start()`: once the client has
   * fallen back to mock mode, `start()` is idempotent and will not probe
   * `/health` again. User-facing retry controls need a real reprobe so
   * opening the UI before the local agent starts does not permanently trap
   * the session in mock mode.
   */
  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  /**
   * Reprobe the local agent without tearing down a usable mock session.
   *
   * Unlike `restart()`, this does not reject pending requests or reset
   * listeners when the agent is still unreachable. It is intended for
   * UI retry loops that should quietly upgrade fallback mock mode to
   * the real HTTP/WS transport as soon as the Python agent appears.
   */
  async reprobe(): Promise<boolean> {
    if (this.forceMock) return true;
    const reachable = await isAgentReachable(this.baseUrl);
    if (!reachable) return false;
    this.mode = "http";
    this.started = true;
    this.startResolve?.();
    if (!this.ws) this.connectWs();
    return true;
  }

  /**
   * Send a JSON-RPC request and await its response.
   *
   *  - In pending mode: waits for `start()` to resolve first.
   *  - In mock mode: dispatches to the in-process mock backend.
   *  - In HTTP mode: `POST /rpc`, returns `result`, throws `IPCError`
   *    on `error` envelope.
   */
  async request<T = unknown>(
    method: string,
    params?: unknown,
    id?: JsonRpcId,
  ): Promise<T> {
    // Block until start() resolves the mode — prevents the
    // ERR_CONNECTION_REFUSED flood that happens when components
    // fire IPC calls before the /health probe completes.
    if (this.mode === "pending") {
      await this.startPromise;
    }

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
    // Pending: start() hasn't resolved yet — silently drop the
    // notification.  Since notify() is fire-and-forget by design,
    // queuing would add complexity for no real user benefit.
    if (this.mode === "pending") return;
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
    if (this.mode === "pending") {
      await this.startPromise;
    }
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
      // Reset backoff on successful connect so the next reconnect
      // cycle starts fresh at 250ms.  The monotonic growth is only
      // needed across *consecutive failures* — once we've connected
      // the sequence should begin again from the base.
      this.wsReconnectAttempts = 0;
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
    if (typeof env.event === "string" && env.event.length > 0) {
      const set = this.listeners.get(env.event);
      if (!set || set.size === 0) return;
      const evt: JsonRpcEvent = {
        jsonrpc: "2.0",
        event: env.event,
        data: env.data,
      };
      for (const cb of set) {
        cb(evt);
      }
      return;
    }

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

    // Heartbeat ping — reply with pong to keep the connection alive.
    if (method === "agent.ping" && this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ jsonrpc: "2.0", method: "agent.pong" }));
      return;
    }

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
    // Per-method timeout — streaming agent calls can run for minutes
    // while the frontend receives progress via WebSocket; fast CRUD
    // methods keep the default 30 s ceiling.
    const timeout = LONG_RUNNING_METHODS[method] ?? 30_000;
    const controller = new AbortController();
    const timer = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;
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
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError" && timeout > 0) {
        throw new IPCError({
          code: ErrorCode.InternalError,
          message: `request timed out (${timeout / 1000}s): ${method}`,
        });
      }
      throw new IPCError({
        code: ErrorCode.InternalError,
        message: `network error: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      if (timer) clearTimeout(timer);
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
  listSessions(opts?: { archived?: boolean; limit?: number; offset?: number; search?: string }): Promise<ListSessionsResult>;
  createSession(opts?: {
    title?: string;
    reuse_empty_session_id?: string;
    model_id?: string;
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
  listMessages(sessionId: string, opts?: { limit?: number; before?: string }): Promise<ListMessagesResult>;
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
  patchPreview(opts?: { scope?: "staged" | "branch" | "working"; ref?: string }): Promise<PatchPreviewResult>;
  patchApplyHunk(opts: PatchHunkOperationParams): Promise<PatchHunkOperationResult>;
  patchRevertHunk(opts: PatchHunkOperationParams): Promise<PatchHunkOperationResult>;
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
    listMessages: (sid, opts) =>
      client.request<ListMessagesResult>("message.list", {
        session_id: sid,
        ...(opts ?? {}),
      }),
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
    patchPreview: (opts) => client.request<PatchPreviewResult>("patch.preview", opts ?? {}),
    patchApplyHunk: (opts) => client.request<PatchHunkOperationResult>("patch.apply_hunk", opts),
    patchRevertHunk: (opts) => client.request<PatchHunkOperationResult>("patch.revert_hunk", opts),
    startTerminal: (opts) => client.request<TerminalStartResult>("terminal.start", opts),
    readTerminal: (opts) => client.request<TerminalReadResult>("terminal.read", opts),
    stopTerminal: (sessionId) => client.request<TerminalStartResult>("terminal.stop", { session_id: sessionId }),
    listTerminals: () => client.request<TerminalListResult>("terminal.list", {}),
    listRunners: () => client.request<RunnerListResult>("runner.list", {}),
    startRunner: (opts) => client.request<RunnerStartResult>("runner.start", opts),

    listAudit: (opts) => client.request<ListAuditResult>("audit.list", opts ?? {}),
    auditStats: () => client.request<AuditStats>("audit.stats", {}),
    purgeAudit: (beforeIso) => client.request<{ deleted: number }>("audit.purge", { before_iso: beforeIso }),

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
const mockSessionsWithMessages = new Set<string>();
const mockRuns = new Map<string, { run: import("../types/ipc").AgentRun; steps: import("../types/ipc").AgentRunStep[] }>();
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

// v0.8.0 — mock teams
const mockTeams: AgentTeam[] = [
  {
    id: "team_mock_fullstack",
    name: "fullstack-team",
    description: "A full-stack review team",
    icon: "Users",
    color: "#6366f1",
    agents: ["general", "researcher"],
    orchestration_mode: "parallel",
    enabled: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

/**
 * Mock plugin registry — mirrors what the Python agent's PluginLoader
 * discovers on disk. Lets the Settings → Plugins tab render in a plain
 * browser. Real data arrives via the ``plugins.*`` IPC namespace.
 */
const mockPlugins: PluginInfo[] = [
  {
    name: "code-linter",
    version: "0.1.0",
    description: "Pre-edit lint hook for Python and TypeScript.",
    author: "minimax-code",
    homepage: "",
    enabled: true,
    enabled_on_disk: true,
    ok: true,
    error: null,
    path: "agent/plugins/code-linter/plugin.json",
    loaded_at: "2026-07-18T00:00:00",
    has_hooks: true,
    has_mcp: false,
    has_permissions: false,
    entry: null,
  },
  {
    name: "broken-example",
    version: "0.0.0",
    description: "",
    author: "",
    homepage: "",
    enabled: false,
    enabled_on_disk: false,
    ok: false,
    error: "invalid manifest: missing required field `name`",
    path: "agent/plugins/broken-example/plugin.json",
    loaded_at: "",
    has_hooks: false,
    has_mcp: false,
    has_permissions: false,
    entry: null,
  },
];

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
const mockTerminalSessions = new Map<string, TerminalSession>();
const mockTerminalChunks = new Map<string, TerminalChunk[]>();
const mockRunners: RunnerInfo[] = [
  {
    id: "native",
    label: "Native shell",
    kind: "native",
    available: true,
    command: null,
    version: null,
    reason: null,
    supports_prompt: false,
    supports_terminal: true,
  },
  {
    id: "codex-cli",
    label: "Codex CLI",
    kind: "external_cli",
    available: false,
    command: null,
    version: null,
    reason: "codex executable not found on PATH",
    supports_prompt: true,
    supports_terminal: true,
  },
  {
    id: "claude-code-cli",
    label: "Claude Code CLI",
    kind: "external_cli",
    available: true,
    command: "claude",
    version: "2.1.168",
    reason: null,
    supports_prompt: true,
    supports_terminal: true,
  },
];

function makeMockTerminalSession(opts: {
  command: string;
  cwd?: string;
  session_id?: string | null;
  output?: string;
}): TerminalSession {
  const now = Date.now() / 1000;
  const id = `term_mock_${Math.random().toString(36).slice(2, 10)}`;
  const session: TerminalSession = {
    id,
    command: opts.command,
    cwd: opts.cwd ?? "",
    session_id: opts.session_id ?? null,
    run_id: opts.session_id ? `run_mock_${Math.random().toString(36).slice(2, 10)}` : null,
    status: "completed",
    started_at: now,
    updated_at: now,
    completed_at: now,
    exit_code: 0,
    error: null,
    next_seq: 2,
  };
  const chunks: TerminalChunk[] = [
    {
      seq: 1,
      stream: "stdout",
      text: opts.output ?? `$ ${opts.command}\n(mock terminal output)\n`,
      received_at: now,
    },
  ];
  mockTerminalSessions.set(id, session);
  mockTerminalChunks.set(id, chunks);
  return session;
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
        reusable.title = p?.title ?? "New task";
        reusable.updated_at = Date.now();
        return { session_id: reusable.id, session: reusable, reused: true };
      }

      const sid = `ses_${Math.random().toString(36).slice(2, 10)}`;
      const now = Date.now();
      const session: Session = {
        id: sid,
        title: p?.title ?? "New task",
        archived: false,
        created_at: now,
        updated_at: now,
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
        title: p?.title ?? "Worktree task",
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
      const p = params as { archived?: boolean; limit?: number; offset?: number; search?: string } | undefined;
      const search = p?.search?.trim().toLowerCase() ?? "";
      const filtered = Array.from(mockSessions.values())
        .filter((s) => (p?.archived === undefined ? true : s.archived === Boolean(p.archived)))
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

    case "message.list": {
      return { messages: [] as ProtocolMessage[] };
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
              metadata: { iterations: 1 },
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

    // ── audit.* mock ──────────────────────────────────────────────

    case "audit.list":
      return { entries: [], total: 0 } satisfies ListAuditResult;

    case "audit.stats":
      return { total: 0, by_tool: {}, by_status: {} } satisfies AuditStats;

    case "audit.purge":
      return { deleted: 0 };

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

/* ─────────────────────── Module-level singleton ─────────────────────── */

/** Singleton for the app — http transport when the agent is up, mock otherwise. */
export const ipc = new IPCClient();

/** Typed binding on top of the singleton. */
export const typedIPC: TypedIPC = bindTypedIPC(ipc);

export { ErrorCode };
