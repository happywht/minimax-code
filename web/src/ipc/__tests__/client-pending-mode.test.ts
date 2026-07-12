/**
 * Tests for the "pending" mode of `IPCClient`.
 *
 * Before `start()` resolves, the client sits in "pending" mode.
 * This prevents the ERR_CONNECTION_REFUSED console-spam that
 * occurs when components fire useEffect IPC calls before the
 * async `/health` probe completes.
 *
 * Coverage:
 *   - constructor defaults to "pending" (not "http")
 *   - isMock returns true during pending
 *   - request() blocks until start() resolves
 *   - request() proceeds via HTTP after start() succeeds
 *   - request() proceeds via mock after start() fails
 *   - multiple concurrent start() calls trigger one probe
 *   - notify() silently drops in pending mode
 *   - ping() blocks until start() resolves
 *   - stop() resets pending state so a new start() works
 *   - forced-mock mode skips pending entirely
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  IPCClient,
  type JsonRpcResponse,
} from "..";

/* ────────── fetch stub ────────── */

type FetchHandler = (
  url: string,
  init: RequestInit,
) => Promise<Response> | Response;

function installFetch(handler: FetchHandler): void {
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    return Promise.resolve(handler(url, init ?? {}));
  }) as unknown as typeof fetch;
}

function uninstallFetch(): void {
  // @ts-expect-error — clearing the stub
  delete globalThis.fetch;
}

function okJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/* ────────── WebSocket stub (minimal) ────────── */

class StubWebSocket {
  static instances: StubWebSocket[] = [];
  static reset(): void { StubWebSocket.instances = []; }
  url: string;
  readyState = 0;
  constructor(url: string) {
    this.url = url;
    StubWebSocket.instances.push(this);
  }
  addEventListener() { /* noop */ }
  removeEventListener() { /* noop */ }
  send() { /* noop */ }
  close() { this.readyState = 3; }
  simulateOpen(): void { this.readyState = 1; }
}

/* ────────── setup / teardown ────────── */

let originalWebSocket: typeof globalThis.WebSocket;
let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  StubWebSocket.reset();
  originalWebSocket = globalThis.WebSocket;
  (globalThis as unknown as { WebSocket: unknown }).WebSocket =
    StubWebSocket as unknown as typeof WebSocket;
  originalFetch = globalThis.fetch;
});

afterEach(async () => {
  (globalThis as unknown as { WebSocket: typeof globalThis.WebSocket }).WebSocket =
    originalWebSocket;
  if (originalFetch) {
    globalThis.fetch = originalFetch;
  } else {
    uninstallFetch();
  }
});

/* ────────── Tests ────────── */

describe("IPCClient pending mode", () => {
  it("constructor defaults to pending mode (not http)", () => {
    const client = new IPCClient();
    // Private field — test via the public getter.
    expect(client.isMock).toBe(true); // pending is treated as mock
    expect(client.isHttp).toBe(false);
  });

  it("isMock returns true during pending", () => {
    const client = new IPCClient();
    expect(client.isMock).toBe(true);
  });

  it("forced-mock mode skips pending entirely", () => {
    const client = new IPCClient({ forceMock: true });
    expect(client.isMock).toBe(true);
    expect(client.isForcedMock).toBe(true);
    // start() should be a no-op — mode is already "mock".
    // No network request should be made.
  });

  it("request() blocks until start() resolves (agent unreachable)", async () => {
    let probeCalled = false;
    installFetch((_url) => {
      probeCalled = true;
      // Simulate agent unreachable
      return Promise.reject(new Error("ECONNREFUSED"));
    });

    const client = new IPCClient();

    // request() should block — it won't resolve until start() completes.
    let requestResolved = false;
    const requestPromise = client.request("session.list").then(() => {
      requestResolved = true;
    });

    // Give the microtask queue a tick — request should still be pending.
    await new Promise((r) => setTimeout(r, 10));
    expect(requestResolved).toBe(false);

    // Now start the client — probe will fail → mock mode.
    await client.start();
    expect(probeCalled).toBe(true);

    // The queued request should now resolve (via mock backend).
    await requestPromise;
    expect(requestResolved).toBe(true);
  });

  it("request() proceeds via HTTP after start() succeeds", async () => {
    let rpcCalled = false;
    installFetch((url, init) => {
      if (url.endsWith("/health")) {
        return okJson({ ok: true });
      }
      // RPC endpoint
      rpcCalled = true;
      const body = JSON.parse(init.body as string);
      return okJson({
        jsonrpc: "2.0",
        id: body.id,
        result: { sessions: [], total: 0 },
      } satisfies JsonRpcResponse);
    });

    const client = new IPCClient();
    await client.start();
    expect(client.isHttp).toBe(true);

    const result = await client.request<{ sessions: unknown[] }>("session.list");
    expect(rpcCalled).toBe(true);
    expect(result.sessions).toEqual([]);
  });

  it("request() proceeds via mock after start() fails (agent down)", async () => {
    installFetch(() => Promise.reject(new Error("ECONNREFUSED")));

    const client = new IPCClient();
    await client.start();
    expect(client.isMock).toBe(true);

    // Mock backend handles session.list
    const result = await client.request<{ sessions: unknown[] }>("session.list");
    expect(result).toBeDefined();
    // mockHandle returns a sessions array
    expect(result).toHaveProperty("sessions");
  });

  it("multiple concurrent start() calls trigger only one probe", async () => {
    let probeCount = 0;
    installFetch((url) => {
      if (url.endsWith("/health")) {
        probeCount++;
        return okJson({ ok: true });
      }
      return okJson({ jsonrpc: "2.0", id: 1, result: {} });
    });

    const client = new IPCClient();

    // Fire 5 concurrent start() calls
    await Promise.all([
      client.start(),
      client.start(),
      client.start(),
      client.start(),
      client.start(),
    ]);

    expect(probeCount).toBe(1);
    expect(client.isHttp).toBe(true);
  });

  it("notify() silently drops in pending mode", async () => {
    let fetchCalled = false;
    installFetch(() => {
      fetchCalled = true;
      return okJson({});
    });

    const client = new IPCClient();
    // Mode is pending — notify should be a no-op.
    await client.notify("test.event", { foo: "bar" });

    // Give microtasks a chance to settle
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchCalled).toBe(false);
  });

  it("ping() blocks until start() resolves", async () => {
    // Simulate a slow /health probe that takes a real delay.
    // ping() is called before start() resolves — it must wait.
    let _healthCallTime = 0;
    installFetch((url) => {
      if (url.endsWith("/health")) {
        _healthCallTime = Date.now();
        void _healthCallTime;
        // Return a response after a short delay
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(okJson({ ok: true })), 30);
        });
      }
      return okJson({ ok: true });
    });

    const client = new IPCClient();

    // Fire start() — probe takes 30ms
    const startPromise = client.start();

    // Immediately fire ping() — it should wait for start()
    const pingResult = await client.ping();

    // start() must also resolve
    await startPromise;

    expect(pingResult).toBe(true);
    expect(client.isHttp).toBe(true);
  });

  it("stop() resets pending state so a new start() works", async () => {
    let probeCount = 0;
    installFetch((url) => {
      if (url.endsWith("/health")) {
        probeCount++;
        return Promise.reject(new Error("ECONNREFUSED"));
      }
      return okJson({});
    });

    const client = new IPCClient();
    await client.start();
    expect(client.isMock).toBe(true);
    expect(probeCount).toBe(1);

    // Stop resets state
    await client.stop();

    // After stop, isMock should still reflect the last mode until
    // a new start() is called.  But the internal started flag is reset,
    // so a new start() will probe again.
    await client.start();
    expect(probeCount).toBe(2); // Second probe was made
  });

  it("restart() reprobes after mock fallback and can switch to HTTP", async () => {
    let probeCount = 0;
    let rpcCalled = false;
    installFetch((url, init) => {
      if (url.endsWith("/health")) {
        probeCount++;
        if (probeCount === 1) {
          return Promise.reject(new Error("ECONNREFUSED"));
        }
        return okJson({ ok: true });
      }
      rpcCalled = true;
      const body = JSON.parse(init.body as string);
      return okJson({
        jsonrpc: "2.0",
        id: body.id,
        result: { sessions: [], total: 0 },
      } satisfies JsonRpcResponse);
    });

    const client = new IPCClient();
    await client.start();
    expect(client.isMock).toBe(true);
    expect(client.isHttp).toBe(false);

    await client.restart();

    expect(probeCount).toBe(2);
    expect(client.isHttp).toBe(true);
    expect(StubWebSocket.instances).toHaveLength(1);

    const result = await client.request<{ sessions: unknown[] }>("session.list");
    expect(rpcCalled).toBe(true);
    expect(result.sessions).toEqual([]);
  });

  it("reprobe() upgrades fallback mock mode without clearing listeners", async () => {
    let probeCount = 0;
    let sawReady = false;
    installFetch((url) => {
      if (url.endsWith("/health")) {
        probeCount++;
        if (probeCount === 1) {
          return Promise.reject(new Error("ECONNREFUSED"));
        }
        return okJson({ ok: true });
      }
      return okJson({ jsonrpc: "2.0", id: 1, result: {} });
    });

    const client = new IPCClient();
    client.on("agent.message_chunk", () => {
      sawReady = true;
    });

    await client.start();
    expect(client.isMock).toBe(true);
    expect(await client.reprobe()).toBe(true);

    expect(client.isHttp).toBe(true);
    expect(StubWebSocket.instances).toHaveLength(1);
    client._emit("agent.message_chunk", { delta: "kept listener" });
    expect(sawReady).toBe(true);
  });

  it("no POST /rpc errors when components fire requests before start()", async () => {
    const urls: string[] = [];
    installFetch((url) => {
      urls.push(url);
      if (url.endsWith("/health")) {
        // Simulate slow probe
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(okJson({ ok: true })), 50);
        });
      }
      return okJson({
        jsonrpc: "2.0",
        id: 1,
        result: { sessions: [] },
      });
    });

    const client = new IPCClient();

    // Simulate multiple components firing requests immediately
    const req1 = client.request("session.list");
    const req2 = client.request("model.list");
    const req3 = client.request("git.status");

    // Start the client (probe takes 50ms)
    const startPromise = client.start();

    // All requests + start should resolve successfully
    await Promise.all([req1, req2, req3, startPromise]);

    // The ONLY fetch call before start() resolves should be
    // the single /health probe — no /rpc calls in the pending window.
    const healthCalls = urls.filter((u) => u.endsWith("/health")).length;
    const rpcCalls = urls.filter((u) => u.endsWith("/rpc")).length;

    expect(healthCalls).toBe(1); // single probe
    expect(rpcCalls).toBe(3); // all 3 requests after mode resolved
  });

  it("does not abort agent.send_message after a fixed client deadline", async () => {
    let resolveRpc: ((response: Response) => void) | undefined;
    let rpcSignal: AbortSignal | undefined;
    let requestId: string | number | null = null;

    installFetch((url, init) => {
      if (url.endsWith("/health")) return okJson({ ok: true });
      rpcSignal = init.signal as AbortSignal;
      requestId = JSON.parse(init.body as string).id;
      return new Promise<Response>((resolve) => {
        resolveRpc = resolve;
      });
    });

    const client = new IPCClient();
    await client.start();
    vi.useFakeTimers();
    try {
      const request = client.request<{ text: string }>("agent.send_message", {
        session_id: "session-long",
        content: "keep working",
      });
      await Promise.resolve();

      await vi.advanceTimersByTimeAsync(600_000);
      expect(rpcSignal?.aborted).toBe(false);

      resolveRpc?.(okJson({
        jsonrpc: "2.0",
        id: requestId,
        result: { text: "done" },
      } satisfies JsonRpcResponse));
      await expect(request).resolves.toEqual({ text: "done" });
    } finally {
      vi.useRealTimers();
      client.stop();
    }
  });
});
