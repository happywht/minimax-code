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
 * Module layout (after the ipc-module restructure):
 *   - `client.ts`  — this file: transport + `ipc` singleton.
 *   - `typed.ts`   — `TypedIPC` interface + `bindTypedIPC` factory.
 *   - `mock.ts`    — in-process mock backend (`mockHandle` switch).
 *   - `mockData.ts`— fixtures / in-memory state used by the mock.
 *
 * See `docs/v0.2.0-web-architecture.md` for the protocol details.
 */

import {
  ErrorCode,
  StreamEvent,
  type JsonRpcError,
  type JsonRpcEvent,
  type JsonRpcId,
  type JsonRpcRequest,
  type JsonRpcResponse,
  type MessageChunkData,
  type PermissionRequestData,
  type PermissionResolvedData,
  type RunCompletedData,
  type RunCreatedData,
  type RunStepData,
  type SidecarEvent,
  type TaskProgressData,
  type ToolCallData,
  type ToolResultData,
} from "../types/ipc";
import { mockNotify, mockRequest } from "./mock";
import { bindTypedIPC, type TypedIPC } from "./typed";
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

/** Shape of the agent's `GET /health` response body. */
export interface AgentHealth {
  ok: boolean;
  db: boolean;
  version: string;
  uptime_s: number;
}

/**
 * Fetch the agent's full health payload — unlike `isAgentReachable`
 * this also reads the body, so callers can surface a degraded storage
 * layer (`db: false`). Returns `null` when the agent is unreachable,
 * responds non-2xx, or returns an unparseable body; callers should
 * treat that as "unknown" (not degraded) and let the connection
 * banner own the unreachable case.
 */
export async function fetchHealth(baseUrl?: string): Promise<AgentHealth | null> {
  const url = baseUrl && baseUrl.length > 0 ? baseUrl : runtimeAgentBaseUrl();
  try {
    const resp = await fetch(`${url.replace(/\/+$/, "")}/health`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) return null;
    const body: unknown = await resp.json();
    if (typeof body !== "object" || body === null) return null;
    const record = body as Record<string, unknown>;
    if (typeof record.db !== "boolean") return null;
    return {
      ok: typeof record.ok === "boolean" ? record.ok : true,
      db: record.db,
      version: typeof record.version === "string" ? record.version : "",
      uptime_s: typeof record.uptime_s === "number" ? record.uptime_s : 0,
    };
  } catch {
    return null;
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
  /**
   * Sequence number of the last broadcast event received over the
   * WebSocket. Sent as `?since=` on reconnect so the agent replays
   * whatever was published while we were disconnected (v0.13.0).
   */
  private wsLastSeq = 0;
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
    // Resume cursor: only a client that has actually seen a sequenced
    // event (wsLastSeq > 0) asks for a replay — a first-ever connect
    // sends no cursor, so a fresh page load doesn't get the whole
    // history ring re-streamed on top of the RPC-fetched state.
    const resume = this.wsLastSeq > 0 ? `?since=${this.wsLastSeq}` : "";
    const wsUrl = `${toWebSocketUrl(this.baseUrl, "/ws")}${resume}`;
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
    // v0.13.0 — track the broadcast sequence so a reconnect can ask
    // the agent to replay everything published after it.
    if (typeof env.seq === "number" && env.seq > this.wsLastSeq) {
      this.wsLastSeq = env.seq;
    }
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
/* ─────────────────────── Module-level singleton ─────────────────────── */

/** Singleton for the app — http transport when the agent is up, mock otherwise. */
export const ipc = new IPCClient();

/** Typed binding on top of the singleton. */
export const typedIPC: TypedIPC = bindTypedIPC(ipc);

export { bindTypedIPC } from "./typed";
export type { TypedIPC } from "./typed";

export { ErrorCode };