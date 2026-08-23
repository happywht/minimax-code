/**
 * Tests for the typed IPC layer — the mock backend, the typed
 * wrappers, and the IPCError class.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
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

  it("agent.send_message creates mock run history", async () => {
    const t = bindTypedIPC(client);
    const sent = await t.sendMessage({ session_id: null, content: "timeline" });
    expect(sent.run_id).toMatch(/^run_/);
    await new Promise((res) => setTimeout(res, 180));

    const runs = await t.listRuns({ session_id: sent.session_id });
    expect(runs.runs.find((r) => r.id === sent.run_id)?.status).toBe("completed");

    const detail = await t.getRunSteps(sent.run_id as string);
    expect(detail.steps.map((s) => s.kind)).toContain("final");
  });

  it("dispatches event envelopes by event name", () => {
    const seen: unknown[] = [];
    client.on("run.created", (env) => seen.push(env.data));
    // Private method, exercised intentionally to pin the WS envelope
    // compatibility path used by the Python agent.
    (client as unknown as { handleEnvelope: (env: Record<string, unknown>) => void })
      .handleEnvelope({
        jsonrpc: "2.0",
        event: "run.created",
        data: { run: { id: "run_1" } },
      });
    expect(seen).toEqual([{ run: { id: "run_1" } }]);
  });

  it("throws an IPCError with code when the underlying request fails", async () => {
    // We can't actually make the mock backend throw, so we test the
    // IPCError constructor directly here.
    const err = new IPCError({ code: -32603, message: "InternalError", data: { foo: 1 } });
    expect(err.code).toBe(-32603);
    expect(err.message).toContain("InternalError");
    expect(err.message).toContain("foo");
    expect(err.data).toEqual({ foo: 1 });
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
    expect(typeof t.listRuns).toBe("function");
    expect(typeof t.patchPreview).toBe("function");
  });

  it("patchPreview returns an empty structured preview in mock mode", async () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);
    const r = await t.patchPreview({ scope: "working" });
    expect(r.scope).toBe("working");
    expect(r.stats).toEqual({ files: 0, additions: 0, deletions: 0 });
    expect(r.files).toEqual([]);
  });

  it("patchApplyFile / patchRevertFile / patchApplyAll / patchRevertAll return ok in mock mode", async () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);

    const applyFile = await t.patchApplyFile({ scope: "working", file_path: "a.py" });
    expect(applyFile).toEqual({
      ok: true,
      operation: "apply_file",
      scope: "working",
      file_path: "a.py",
    });

    const revertFile = await t.patchRevertFile({ scope: "working", file_path: "a.py" });
    expect(revertFile).toEqual({
      ok: true,
      operation: "revert_file",
      scope: "working",
      file_path: "a.py",
    });

    const applyAll = await t.patchApplyAll({ scope: "working" });
    expect(applyAll).toMatchObject({ ok: true, operation: "apply_all", scope: "working" });

    const revertAll = await t.patchRevertAll({ scope: "working" });
    expect(revertAll).toMatchObject({ ok: true, operation: "revert_all", scope: "working" });
  });

  it("patchSaveSnapshot returns clean in mock mode", async () => {
    const client = new IPCClient({ forceMock: true });
    const t = bindTypedIPC(client);
    const r = await t.patchSaveSnapshot();
    expect(r).toEqual({ ok: true, snapshot_ref: null, clean: true });
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
  // Tauri was dropped in v0.2.0 — the shell no longer exists and
  // ``isTauri()`` is a constant ``false``. The legacy
  // ``__TAURI_INTERNALS__`` global is irrelevant; we just pin the
  // contract so a future "we're back inside a Tauri shell" change
  // has to update this test on purpose.
  it("always returns false (Tauri removed in v0.2.0)", () => {
    const w = window as unknown as Record<string, unknown>;
    w.__TAURI_INTERNALS__ = {};
    expect(isTauri()).toBe(false);
  });

  it("does not crash when __TAURI_INTERNALS__ is unset", () => {
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

describe("seq epoch reset (agent.ready next_seq anchor, v1.2.2)", () => {
  type Priv = {
    wsLastSeq: number;
    handleEnvelope: (env: Record<string, unknown>) => void;
    ws: { close: (code?: number, reason?: string) => void } | null;
  };

  function makeClient(startSeq: number) {
    const client = new IPCClient({ forceMock: true });
    const priv = client as unknown as Priv;
    priv.wsLastSeq = startSeq;
    priv.ws = { close: vi.fn() };
    return { client, priv };
  }

  function readyFrame(nextSeq?: number) {
    return {
      jsonrpc: "2.0",
      method: "agent.ready",
      params: { server: "minimax-code-agent", version: "t", ...(nextSeq === undefined ? {} : { next_seq: nextSeq }) },
    };
  }

  it("resets the watermark and reconnects when the anchor is at or behind it", () => {
    // The v1.2.2 regression: agent restarted, client still holds the
    // previous process's watermark (47); the fresh process anchors at
    // 1 — 1 <= 47 means the ?since=47 cursor points into a dead epoch.
    const { priv } = makeClient(47);
    priv.handleEnvelope(readyFrame(1));
    expect(priv.wsLastSeq).toBe(0);
    expect(priv.ws?.close).toHaveBeenCalledWith(1000, "seq-epoch-reset");
  });

  it("keeps the watermark when the anchor is ahead (same process)", () => {
    const { priv } = makeClient(47);
    priv.handleEnvelope(readyFrame(48));
    expect(priv.wsLastSeq).toBe(47);
    expect(priv.ws?.close).not.toHaveBeenCalled();
  });

  it("keeps the watermark once the new epoch has run past it", () => {
    // After a restart the fresh process may have already broadcast
    // past the old watermark — the old cursor is naturally valid then,
    // so the comparison (not a mere restart) must drive the reset.
    const { priv } = makeClient(47);
    priv.handleEnvelope(readyFrame(61));
    expect(priv.wsLastSeq).toBe(47);
    expect(priv.ws?.close).not.toHaveBeenCalled();
  });

  it("does nothing on a first-ever connect (watermark 0)", () => {
    const { priv } = makeClient(0);
    priv.handleEnvelope(readyFrame(1));
    expect(priv.wsLastSeq).toBe(0);
    expect(priv.ws?.close).not.toHaveBeenCalled();
  });

  it("ignores ready frames without an anchor (older servers)", () => {
    const { priv } = makeClient(47);
    priv.handleEnvelope(readyFrame());
    expect(priv.wsLastSeq).toBe(47);
    expect(priv.ws?.close).not.toHaveBeenCalled();
  });

  it("still advances the watermark on live sequenced events", () => {
    const { priv } = makeClient(0);
    priv.handleEnvelope({
      jsonrpc: "2.0",
      method: "agent.status",
      params: { status: "thinking" },
      seq: 5,
    });
    expect(priv.wsLastSeq).toBe(5);
  });
});
