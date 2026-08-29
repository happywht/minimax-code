/**
 * useCheckpointStore (R1 / evolution gap closure).
 *
 * The panel's list / diff state used to live in component useState and
 * died on every RightPanel tab switch. These tests pin the store
 * contract that replaces it: per-session list caching (re-mount is
 * free), forced invalidation after create/delete, diff caching keyed
 * by checkpoint id, and honest load-error surfacing.
 *
 * The real ``typed.ts`` bindings run against a spied ``client.request``
 * (same harness as project-root-scope.test.ts) so assertions cover
 * both the wire shape and the store behaviour.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// Mutable per-test wire table — tests reassign entries on ``wire``
// (same object the mock closure reads).
const wire: Record<string, unknown> = {
  "checkpoint.list": { checkpoints: [] },
  "checkpoint.diff": { available: true, patch: "--- a\n+++ b\n" },
};

vi.mock("../../ipc/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../ipc/client")>();

  const spyClient = {
    request: vi.fn().mockImplementation((method: string, params?: Record<string, unknown>) => {
      if (method === "checkpoint.list" && params?.session_id === "s_err") {
        return Promise.reject(new Error("boom"));
      }
      if (method === "checkpoint.diff" && params?.checkpoint_id === "ck_bad") {
        return Promise.reject(new Error("diff boom"));
      }
      return Promise.resolve(wire[method] ?? {});
    }),
    start: vi.fn().mockResolvedValue(undefined),
    ping: vi.fn().mockResolvedValue(true),
    on: vi.fn(() => () => {}),
    close: vi.fn(),
  };

  return { ...mod, ipc: spyClient, typedIPC: mod.bindTypedIPC(spyClient as never) };
});

import { ipc } from "../../ipc/client";
import { useCheckpointStore } from "../checkpoint";
import { strings } from "../../ui/strings";
import type { Checkpoint } from "../../types/ipc";

const requestSpy = (ipc as unknown as { request: ReturnType<typeof vi.fn> }).request;

function ckpt(id: string): Checkpoint {
  return {
    id,
    session_id: "s1",
    label: `label-${id}`,
    message: "",
    git_stash_ref: null,
    branch: "main",
    tracked_files: ["a.ts"],
    untracked_files: [],
    has_untracked_snapshot: false,
    created_at: "2026-08-29T00:00:00Z",
  };
}

function wireCalls(method: string): number {
  return requestSpy.mock.calls.filter(([m]) => m === method).length;
}

beforeEach(() => {
  requestSpy.mockClear();
  wire["checkpoint.list"] = { checkpoints: [] };
  wire["checkpoint.diff"] = { available: true, patch: "--- a\n+++ b\n" };
  useCheckpointStore.getState().reset();
});

describe("useCheckpointStore — list caching", () => {
  it("load() fetches once per session; a second load() is a cache hit (no wire call)", async () => {
    wire["checkpoint.list"] = { checkpoints: [ckpt("ck_1")] };

    await useCheckpointStore.getState().load("s1");
    expect(wireCalls("checkpoint.list")).toBe(1);
    expect(useCheckpointStore.getState().bySession.s1?.map((c) => c.id)).toEqual(["ck_1"]);

    await useCheckpointStore.getState().load("s1");
    expect(wireCalls("checkpoint.list")).toBe(1);
  });

  it("load(sessionId, true) forces a re-fetch", async () => {
    await useCheckpointStore.getState().load("s1");
    await useCheckpointStore.getState().load("s1", true);
    expect(wireCalls("checkpoint.list")).toBe(2);
  });

  it("sessions cache independently", async () => {
    await useCheckpointStore.getState().load("s1");
    await useCheckpointStore.getState().load("s2");
    expect(wireCalls("checkpoint.list")).toBe(2);
    expect(Object.keys(useCheckpointStore.getState().bySession).sort()).toEqual(["s1", "s2"]);
  });

  it("a failed load sets loadError and leaves no cached list", async () => {
    await useCheckpointStore.getState().load("s_err");
    const s = useCheckpointStore.getState();
    expect(s.loadError.s_err).toBe("boom");
    expect(s.bySession.s_err).toBeUndefined();
    expect(s.loading.s_err).toBe(false);
  });
});

describe("useCheckpointStore — invalidate", () => {
  it("re-fetches the list and prunes diff entries for vanished checkpoints", async () => {
    await useCheckpointStore.getState().load("s1");
    useCheckpointStore.setState((s) => ({
      diffs: { ...s.diffs, ck_gone: "old patch", ck_keep: "kept patch" },
    }));

    // Fresh backend list no longer contains ck_gone (deleted).
    wire["checkpoint.list"] = { checkpoints: [ckpt("ck_keep")] };

    await useCheckpointStore.getState().invalidate("s1");
    expect(wireCalls("checkpoint.list")).toBe(2);
    expect(useCheckpointStore.getState().bySession.s1?.map((c) => c.id)).toEqual(["ck_keep"]);
    expect(useCheckpointStore.getState().diffs).toEqual({ ck_keep: "kept patch" });
  });
});

describe("useCheckpointStore — diff caching", () => {
  it("ensureDiff fetches once; a cached checkpoint never re-fetches", async () => {
    await useCheckpointStore.getState().ensureDiff("ck_1");
    expect(wireCalls("checkpoint.diff")).toBe(1);
    expect(useCheckpointStore.getState().diffs.ck_1).toBe("--- a\n+++ b\n");

    await useCheckpointStore.getState().ensureDiff("ck_1");
    expect(wireCalls("checkpoint.diff")).toBe(1);
  });

  it("toggleExpand expands + fetches, collapses without re-fetch, re-expand is cached", async () => {
    useCheckpointStore.getState().toggleExpand("ck_1");
    expect(useCheckpointStore.getState().expandedDiffId).toBe("ck_1");
    // The fetch is fire-and-forget — let it settle.
    await vi.waitFor(() => expect(useCheckpointStore.getState().diffs.ck_1).toBeDefined());
    expect(wireCalls("checkpoint.diff")).toBe(1);

    useCheckpointStore.getState().toggleExpand("ck_1");
    expect(useCheckpointStore.getState().expandedDiffId).toBeNull();

    useCheckpointStore.getState().toggleExpand("ck_1");
    expect(useCheckpointStore.getState().expandedDiffId).toBe("ck_1");
    expect(wireCalls("checkpoint.diff")).toBe(1);
  });

  it("a failed diff load caches the error text (no infinite retry on re-expand)", async () => {
    await useCheckpointStore.getState().ensureDiff("ck_bad");
    expect(useCheckpointStore.getState().diffs.ck_bad).toContain("diff boom");
    expect(useCheckpointStore.getState().diffLoading.ck_bad).toBe(false);

    // Second ensure is a cache hit — the error text is sticky.
    await useCheckpointStore.getState().ensureDiff("ck_bad");
    expect(wireCalls("checkpoint.diff")).toBe(1);
  });

  it("an unavailable diff caches the \"no diff\" note instead of the patch", async () => {
    wire["checkpoint.diff"] = { available: false, patch: "" };
    await useCheckpointStore.getState().ensureDiff("ck_empty");
    expect(useCheckpointStore.getState().diffs.ck_empty).toBe(
      strings.rightPanel.checkpoint.noDiff,
    );
  });
});
