/**
 * Permission wire-mapping tests (R18).
 *
 * The Python `permission.*` handlers speak `{tool_pattern, action,
 * scope, created_at: ISO-string}` on the wire; the frontend
 * `PermissionRule` speaks `{tool, pattern, decision, created_at: ms}`.
 * `bindTypedIPC` is the translation layer — these tests pin it down,
 * together with the mock backend's contract (the mock must speak the
 * *wire* shape because it sits below the typed layer).
 *
 * Also pins the R18 factory default: `exec_*` → ask must surface
 * through `permission.list` with `origin: "default"` and disappear
 * once a user rule shadows the pattern.
 */

import { describe, it, expect } from "vitest";
import { backendRuleToFrontend, bindTypedIPC } from "../typed";
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

describe("backendRuleToFrontend", () => {
  it("maps wire fields onto the frontend shape", () => {
    const mapped = backendRuleToFrontend({
      id: "pr_abc",
      tool_pattern: "exec_*",
      action: "allow",
      scope: "global",
      created_at: "2026-08-20T10:00:00Z",
    });
    expect(mapped).toEqual({
      id: "pr_abc",
      tool: "exec_*",
      pattern: "exec_*",
      decision: "allow",
      created_at: Date.parse("2026-08-20T10:00:00Z"),
      origin: undefined,
    });
  });

  it("keeps the default origin and maps empty created_at to 0", () => {
    const mapped = backendRuleToFrontend({
      id: "pr_default_exec",
      tool_pattern: "exec_*",
      action: "ask",
      created_at: "",
      origin: "default",
    });
    expect(mapped.origin).toBe("default");
    expect(mapped.created_at).toBe(0);
    expect(mapped.decision).toBe("ask");
  });
});

describe("bindTypedIPC permission translation", () => {
  it("listRules translates the wire response", async () => {
    const typed = bindTypedIPC(
      fakeClient({
        "permission.list": {
          rules: [
            {
              id: "pr_default_exec",
              tool_pattern: "exec_*",
              action: "ask",
              scope: "global",
              created_at: "",
              origin: "default",
            },
          ],
        },
      }),
    );
    const r = await typed.listRules();
    expect(r.rules).toHaveLength(1);
    expect(r.rules[0].tool).toBe("exec_*");
    expect(r.rules[0].pattern).toBe("exec_*");
    expect(r.rules[0].decision).toBe("ask");
    expect(r.rules[0].origin).toBe("default");
  });

  it("setRule sends wire params (tool_pattern/action), never frontend names", async () => {
    const calls: { method: string; params: unknown }[] = [];
    const typed = bindTypedIPC(
      fakeClient(
        {
          "permission.set": {
            rule: { id: "pr_1", tool_pattern: "exec_*", action: "deny", created_at: "2026-08-20T10:00:00Z" },
          },
        },
        calls,
      ),
    );
    const r = await typed.setRule({ tool: "exec_*", pattern: "exec_*", decision: "deny" });
    expect(calls[0].method).toBe("permission.set");
    expect(calls[0].params).toEqual({
      tool_pattern: "exec_*",
      action: "deny",
      scope: "global",
    });
    // Response translated back to the frontend shape.
    expect(r.rule.decision).toBe("deny");
    expect(r.rule.pattern).toBe("exec_*");
  });
});

describe("mock backend permission contract (R18)", () => {
  // The mock sits *below* the typed layer, so it must speak wire shape.
  const client = new IPCClient();

  it("lists the factory exec_* → ask default on a fresh mock", async () => {
    const r = await mockRequest<{ rules: { tool_pattern: string; action: string; origin?: string }[] }>(
      "permission.list",
      {},
      client,
    );
    const def = r.rules.find((x) => x.tool_pattern === "exec_*");
    expect(def).toBeDefined();
    expect(def!.action).toBe("ask");
    expect(def!.origin).toBe("default");
  });

  it("a user rule shadows the default; deleting it restores the default", async () => {
    await mockRequest("permission.set", { tool_pattern: "exec_*", action: "allow" }, client);
    let r = await mockRequest<{ rules: { tool_pattern: string; action: string; origin?: string }[] }>(
      "permission.list",
      {},
      client,
    );
    expect(r.rules.find((x) => x.tool_pattern === "exec_*")?.origin).toBeUndefined();
    expect(r.rules.find((x) => x.tool_pattern === "exec_*")?.action).toBe("allow");

    await mockRequest("permission.delete", { tool_pattern: "exec_*" }, client);
    r = await mockRequest<{ rules: { tool_pattern: string; action: string; origin?: string }[] }>(
      "permission.list",
      {},
      client,
    );
    expect(r.rules.find((x) => x.tool_pattern === "exec_*")?.origin).toBe("default");
    expect(r.rules.find((x) => x.tool_pattern === "exec_*")?.action).toBe("ask");
  });
});
