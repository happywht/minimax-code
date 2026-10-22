import { afterEach, describe, expect, it, vi } from "vitest";
import { authHeaders, authProtocols, ensureSession, rememberHealth } from "../src/ipc/httpAuth";

afterEach(() => vi.unstubAllGlobals());

describe("browser application authentication", () => {
  it("bootstraps once for concurrent callers and uses headers and WS protocols", async () => {
    const base = "http://auth-one.test";
    await rememberHealth(base, new Response(JSON.stringify({ auth_required: true })));
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ token: "test-token" })));
    vi.stubGlobal("fetch", fetcher);
    await Promise.all([ensureSession(base), ensureSession(base)]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]).toEqual([`${base}/auth/session`, {
      method: "POST", credentials: "include", headers: { "X-Minimax-Client": "web" },
    }]);
    expect(authHeaders(base)).toEqual({ Authorization: "Bearer test-token" });
    expect(authProtocols(base)).toEqual(["minimax-auth.test-token"]);
    await ensureSession(base);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("refreshes a rotated token after a server restart", async () => {
    const base = "http://auth-rotation.test";
    await rememberHealth(base, new Response(JSON.stringify({ auth_required: true })));
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: "before" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: "after" })));
    vi.stubGlobal("fetch", fetcher);
    await ensureSession(base);
    await ensureSession(base, true);
    expect(authHeaders(base)).toEqual({ Authorization: "Bearer after" });
    expect(fetcher.mock.calls[1][1].headers.Authorization).toBe("Bearer before");
  });

  it("surfaces rejection and permits a later retry without inventing credentials", async () => {
    const base = "http://auth-failure.test";
    await rememberHealth(base, new Response(JSON.stringify({ auth_required: true })));
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("denied", { status: 403 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: "recovered" })));
    vi.stubGlobal("fetch", fetcher);
    await expect(ensureSession(base)).rejects.toThrow("403");
    expect(authHeaders(base)).toEqual({});
    await ensureSession(base);
    expect(authProtocols(base)).toEqual(["minimax-auth.recovered"]);
  });
});
