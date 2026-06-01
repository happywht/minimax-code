/**
 * IPC client — wraps Tauri `invoke` and `event` so the rest of the
 * frontend never has to know the wire format.
 *
 * The client maintains a per-id response promise map and exposes
 * `request<T>(method, params?)` and `notify(method, params?)`. Streams
 * (events) are routed to listeners registered with `on(event, cb)`.
 *
 * See `docs/ipc-contract.md` for the protocol.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  ErrorCode,
  type JsonRpcError,
  type JsonRpcEvent,
  type JsonRpcId,
  type JsonRpcRequest,
  type JsonRpcResponse,
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
type SideCarListener = (payload: { status: string; error?: string }) => void;

export class IPCClient {
  private pending = new Map<JsonRpcId, Pending>();
  private listeners = new Map<string, Set<EventListener>>();
  private responseListeners = new Set<ResponseListener>();
  private sideCarListeners = new Set<SideCarListener>();
  private unlistenResponse: UnlistenFn | null = null;
  private unlistenEvent: UnlistenFn | null = null;
  private unlistenSideCar: UnlistenFn | null = null;
  private started = false;

  /** Subscribe to Tauri events exactly once. Idempotent. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

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

    this.unlistenSideCar = await listen<{
      status: string;
      error?: string;
    }>(SIDE_CAR_EVENT, (e) => {
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
    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    // The Rust command takes (method, params, id). We forward the
    // explicit id when the caller supplied one so they can correlate
    // responses on their end.
    await invoke("ipc_request", {
      method,
      params: params ?? null,
      id: id ?? null,
    });
    try {
      return (await promise) as T;
    } catch (err) {
      // Convert JsonRpcError to a JS Error for ergonomic throwing.
      const e = err as JsonRpcError;
      const error = new Error(
        `[${e.code}] ${e.message}` +
          (e.data != null ? `: ${JSON.stringify(e.data)}` : ""),
      );
      (error as Error & { code?: number; data?: unknown }).code = e.code;
      (error as Error & { code?: number; data?: unknown }).data = e.data;
      throw error;
    }
  }

  /** Fire-and-forget notification (no id, no response expected). */
  async notify(method: string, params?: unknown): Promise<void> {
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
      // Forward the envelope to the typed callback.
      cb({ event: env.event, data: env.data as T | undefined });
    };
    set.add(wrapped);
    return () => {
      set?.delete(wrapped);
    };
  }

  onSideCar(cb: SideCarListener): () => void {
    this.sideCarListeners.add(cb);
    return () => {
      this.sideCarListeners.delete(cb);
    };
  }

  /** Liveness probe — true once the Rust bridge has started. */
  async ping(): Promise<boolean> {
    try {
      return (await invoke("ping_agent")) as boolean;
    } catch {
      return false;
    }
  }

  private uuid(): string {
    // Browser crypto.randomUUID is available in Tauri's webview.
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      return crypto.randomUUID();
    }
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}

/** Module-level singleton. */
export const ipc = new IPCClient();

export { ErrorCode };
