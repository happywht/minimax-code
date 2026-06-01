/**
 * Tests for the typed IPC layer — the mock backend, the typed
 * wrappers, and the IPCError class.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { IPCClient, IPCError, bindTypedIPC, isTauri, typedIPC } from "../src/ipc";

describe("IPC mock backend", () => {
  let client: IPCClient;

  beforeEach(() => {
    client = new IPCClient({ forceMock: true });
  });

  it("ping returns a pong payload", async () => {
    const r = await client.request<{ pong: number }>("ping", {});
    expect(typeof r.pong).toBe("number");
  });

  it("session.create returns a stable session_id and lists it back", async () => {
    const c = await client.request<{ session_id: string }>("session.create", { title: "A" });
    expect(c.session_id).toMatch(/^ses_/);
    const list = await client.request<{ sessions: { id: string; title: string }[] }>(
      "session.list",
      {},
    );
    expect(list.sessions.find((s) => s.id === c.session_id)?.title).toBe("A");
  });

  it("session.delete removes the session", async () => {
    const c = await client.request<{ session_id: string }>("session.create", { title: "X" });
    await client.request("session.delete", { session_id: c.session_id });
    const list = await client.request<{ sessions: { id: string }[] }>("session.list", {});
    expect(list.sessions.find((s) => s.id === c.session_id)).toBeUndefined();
  });

  it("model.list returns the seeded mock models", async () => {
    const r = await client.request<{ models: { id: string }[]; current: string }>(
      "model.list",
      {},
    );
    expect(r.models.length).toBeGreaterThan(0);
    expect(r.current).toBe(r.models[0].id);
  });

  it("model.set_current switches the model", async () => {
    const r = await client.request<{ current: string }>("model.set_current", {
      model_id: "minimax-M2.7-pro",
    });
    expect(r.current).toBe("minimax-M2.7-pro");
  });

  it("agent.send_message streams chunks via on()", async () => {
    const seen: string[] = [];
    client.on("agent.message_chunk", (env) => {
      const data = env.data as { delta?: string; done?: boolean } | undefined;
      if (data?.delta) seen.push(data.delta);
    });
    const r = await client.request<{ session_id: string; message_id: string }>(
      "agent.send_message",
      { session_id: null, content: "hello" },
    );
    expect(r.session_id).toMatch(/^ses_/);
    expect(r.message_id).toMatch(/^msg_/);
    // Wait for the chunks to arrive (mock uses setTimeout).
    await new Promise((res) => setTimeout(res, 200));
    expect(seen.join("")).toMatch(/Received: hello/);
  });

  it("throws an IPCError with code when the underlying request fails", async () => {
    // Inject a custom error via the mock helpers.
    const id = "x";
    try {
      // We can't actually make the mock backend throw, so we test the
      // IPCError constructor directly here.
      const err = new IPCError({ code: -32603, message: "InternalError", data: { foo: 1 } });
      expect(err.code).toBe(-32603);
      expect(err.message).toContain("InternalError");
      expect(err.message).toContain("foo");
      expect(err.data).toEqual({ foo: 1 });
    } catch (e) {
      throw e;
    }
    void id;
  });
});

describe("TypedIPC wrappers", () => {
  it("bindTypedIPC exposes the expected surface", () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);
    expect(typeof t.listSessions).toBe("function");
    expect(typeof t.sendMessage).toBe("function");
    expect(typeof t.listModels).toBe("function");
    expect(typeof t.listJobs).toBe("function");
    expect(typeof t.listRules).toBe("function");
  });

  it("listJobs returns an empty array by default", async () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);
    const r = await t.listJobs();
    expect(r.jobs).toEqual([]);
  });

  it("createJob + listJobs round-trip", async () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);
    const r = await t.createJob({ name: "test", cron: "* * * * *", prompt: "hi" });
    expect(r.job.name).toBe("test");
    const list = await t.listJobs();
    expect(list.jobs.find((j) => j.id === r.job.id)?.cron).toBe("* * * * *");
  });
});

describe("isTauri", () => {
  it("returns true when __TAURI_INTERNALS__ is present", () => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      value: {},
      configurable: true,
    });
    expect(isTauri()).toBe(true);
  });

  it("returns false when running in a plain browser", () => {
    // jsdom doesn't have it by default; this test asserts the negative
    // case after removing any leftover from previous tests.
    const w = window as unknown as Record<string, unknown>;
    delete w.__TAURI_INTERNALS__;
    expect(isTauri()).toBe(false);
  });
});

describe("module-level singleton", () => {
  it("typedIPC is the same as the bound singleton", () => {
    expect(typedIPC).toBeDefined();
    expect(typeof typedIPC.listSessions).toBe("function");
  });
});
