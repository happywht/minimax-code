/**
 * Skill invoke wire-contract tests (v1.2.0 cut 3).
 *
 * The backend `skill.invoke` handler (handlers_skills.py) expects
 * ``skill_id`` + ``request`` as required keys and reads special-route
 * keys (``diff`` for code review) from the *top level* of params.
 * ``invokeSkill`` therefore flattens object args onto the wire next to
 * the canonical ``request`` copy. These tests pin the typed-layer
 * flattening and the mock backend's reply shape (mock must speak the
 * wire shape because it sits below the typed layer).
 */

import { describe, it, expect } from "vitest";
import { bindTypedIPC } from "../typed";
import { mockRequest } from "../mock";
import { IPCClient } from "../client";
import type { IPCClient as IPCClientType } from "../client";

/** Minimal fake client that records wire traffic and replays canned replies. */
function fakeClient(
  replies: Record<string, unknown>,
  calls: { method: string; params: unknown }[] = [],
): IPCClientType {
  return {
    request: (method: string, params: unknown) => {
      calls.push({ method, params });
      return Promise.resolve(replies[method]);
    },
  } as unknown as IPCClientType;
}

describe("invokeSkill arg flattening (typed layer)", () => {
  it("flattens object args onto the wire so skill routes see top-level keys", async () => {
    const calls: { method: string; params: unknown }[] = [];
    const api = bindTypedIPC(fakeClient({ "skill.invoke": { ok: true } }, calls));

    await api.invokeSkill("code-review:code-review", { diff: "+++ fake diff" });

    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("skill.invoke");
    expect(calls[0].params).toEqual({
      skill_id: "code-review:code-review",
      request: { diff: "+++ fake diff" },
      diff: "+++ fake diff",
    });
  });

  it("passes non-object args through as the canonical request", async () => {
    const calls: { method: string; params: unknown }[] = [];
    const api = bindTypedIPC(fakeClient({ "skill.invoke": { ok: true } }, calls));

    await api.invokeSkill("commit-helper", "plain prompt text");

    expect(calls[0].params).toEqual({
      skill_id: "commit-helper",
      request: "plain prompt text",
    });
  });

  it("ignores array args instead of spreading them", async () => {
    const calls: { method: string; params: unknown }[] = [];
    const api = bindTypedIPC(fakeClient({ "skill.invoke": { ok: true } }, calls));

    await api.invokeSkill("x", ["a", "b"]);

    expect(calls[0].params).toEqual({ skill_id: "x", request: ["a", "b"] });
  });
});

describe("skill.invoke mock backend reply shape", () => {
  it("replies with the flattened backend envelope on the diff route", async () => {
    const client = new IPCClient();
    const r = (await mockRequest("skill.invoke", {
      skill_id: "code-review:code-review",
      request: { diff: "+++ x" },
      diff: "+++ x",
    }, client)) as Record<string, unknown>;

    expect(r.text).toEqual(expect.any(String));
    expect(r.output).toBe(r.text);
    expect(Array.isArray(r.comments)).toBe(true);
    expect(r.stats).toEqual(
      expect.objectContaining({ files: expect.any(Number) }),
    );
  });

  it("replies with the generic envelope for plain requests", async () => {
    const client = new IPCClient();
    const r = (await mockRequest("skill.invoke", {
      skill_id: "commit-helper",
      request: "draft a commit",
    }, client)) as Record<string, unknown>;

    expect(r.text).toEqual(expect.any(String));
    expect(r.output).toBe(r.text);
    expect(r.iterations).toEqual(expect.any(Number));
  });
});
