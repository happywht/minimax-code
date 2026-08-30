/**
 * Token-injection regression tests (v1.8.0).
 *
 * The IPCClient must attach the stored access token to every RPC
 * request (Authorization header) and WebSocket URL (?token= query),
 * and render a 401 with the actionable auth message.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AGENT_TOKEN_STORAGE_KEY,
  getAgentToken,
  setAgentToken,
} from "../client";

describe("agent token storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("round-trips a token through localStorage", () => {
    expect(getAgentToken()).toBe("");
    setAgentToken("  abc123  ");
    expect(getAgentToken()).toBe("abc123");
    expect(localStorage.getItem(AGENT_TOKEN_STORAGE_KEY)).toBe("abc123");
  });

  it("clearing with an empty string removes the entry", () => {
    setAgentToken("abc123");
    setAgentToken("   ");
    expect(getAgentToken()).toBe("");
    expect(localStorage.getItem(AGENT_TOKEN_STORAGE_KEY)).toBeNull();
  });
});

describe("RPC token injection", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
    const fn = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  async function callRpc(): Promise<{ headers: Headers; call: unknown }> {
    const { IPCClient } = await import("../client");
    const client = new IPCClient({ mockMode: false });
    // Bypass the /health probe: force http mode directly.
    (client as unknown as { mode: string }).mode = "http";
    const promise = client.request("ping");
    const fn = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const call = fn.mock.calls[0];
    const headers = new Headers((call[1] as RequestInit).headers as HeadersInit);
    await promise.catch(() => undefined);
    return { headers, call };
  }

  it("sends Authorization when a token is stored", async () => {
    setAgentToken("sekret-1");
    mockFetch(200, { jsonrpc: "2.0", id: 1, result: "pong" });
    const { headers } = await callRpc();
    expect(headers.get("authorization")).toBe("Bearer sekret-1");
  });

  it("sends no Authorization header when no token is stored", async () => {
    mockFetch(200, { jsonrpc: "2.0", id: 1, result: "pong" });
    const { headers } = await callRpc();
    expect(headers.get("authorization")).toBeNull();
  });

  it("maps a 401 to the actionable auth message", async () => {
    setAgentToken("wrong-token");
    mockFetch(401, { error: "unauthorized" });
    const { IPCClient, IPCError } = await import("../client");
    const client = new IPCClient({ mockMode: false });
    (client as unknown as { mode: string }).mode = "http";
    await expect(client.request("ping")).rejects.toMatchObject({
      name: "IPCError",
      message: expect.stringContaining("401"),
    });
    void IPCError;
  });
});

describe("WebSocket token query", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("appends ?token= to the WS URL when a token is stored", async () => {
    setAgentToken("sekret-2");
    const urlCaptures: string[] = [];
    class FakeWebSocket {
      constructor(url: string) {
        urlCaptures.push(url);
      }
      addEventListener(): void {}
      close(): void {}
      send(): void {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const { IPCClient } = await import("../client");
    const client = new IPCClient({ mockMode: false });
    (client as unknown as { mode: string }).mode = "http";
    (client as unknown as { connectWs: () => void }).connectWs();
    expect(urlCaptures[0]).toContain("token=sekret-2");
  });

  it("omits the token query when none is stored", async () => {
    const urlCaptures: string[] = [];
    class FakeWebSocket {
      constructor(url: string) {
        urlCaptures.push(url);
      }
      addEventListener(): void {}
      close(): void {}
      send(): void {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const { IPCClient } = await import("../client");
    const client = new IPCClient({ mockMode: false });
    (client as unknown as { mode: string }).mode = "http";
    (client as unknown as { connectWs: () => void }).connectWs();
    expect(urlCaptures[0]).not.toContain("token=");
  });
});
