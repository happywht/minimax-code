import { describe, expect, it } from "vitest";
import {
  agentMentionOptions,
  detectMentionToken,
  extractMentionContext,
  fileMentionOptions,
  filterMentionOptions,
  repoMentionOptions,
} from "../src/lib/mentions";
import type { AgentInfo } from "../src/types/ipc";

const AGENTS: AgentInfo[] = [
  { id: "general", name: "General", description: "default helper", enabled: true },
  { id: "code-reviewer", name: "Code Reviewer", description: "reviews diffs", enabled: true },
];

describe("mentions", () => {
  it("detects an @ token at the caret", () => {
    const state = detectMentionToken("please ask @code", "please ask @code".length);
    expect(state.open).toBe(true);
    expect(state.kind).toBe("agent");
    expect(state.query).toBe("code");
    expect(state.anchor).toBe(11);
  });

  it("does not open mention state when the caret is outside an @ token", () => {
    expect(detectMentionToken("email me@example.com", "email me@example.com".length).open).toBe(false);
  });

  it("detects a @repo mention", () => {
    const state = detectMentionToken("search @repo", "search @repo".length);
    expect(state.open).toBe(true);
    expect(state.kind).toBe("repo");
    expect(state.query).toBe("repo");
  });

  it("detects a #file mention", () => {
    const state = detectMentionToken("look at #src/auth", "look at #src/auth".length);
    expect(state.open).toBe(true);
    expect(state.kind).toBe("file");
    expect(state.query).toBe("src/auth");
    expect(state.anchor).toBe(8);
  });

  it("extracts repo and file context requests", () => {
    const ctx = extractMentionContext("@repo explain #src/auth.ts and #src/api.ts");
    expect(ctx.hasRepo).toBe(true);
    expect(ctx.files).toEqual(["src/auth.ts", "src/api.ts"]);
    expect(ctx.cleanText).toBe("explain and");
  });

  it("deduplicates repeated file mentions", () => {
    const ctx = extractMentionContext("#src/auth.ts #src/auth.ts");
    expect(ctx.files).toEqual(["src/auth.ts"]);
  });

  it("returns empty context for plain text", () => {
    const ctx = extractMentionContext("hello world");
    expect(ctx.hasRepo).toBe(false);
    expect(ctx.files).toEqual([]);
    expect(ctx.cleanText).toBe("hello world");
  });

  it("maps agents into searchable mention options", () => {
    const options = agentMentionOptions(AGENTS);
    const matches = filterMentionOptions(options, {
      open: true,
      kind: "agent",
      query: "review",
      cursor: 0,
      anchor: 0,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].id).toBe("code-reviewer");
    expect(matches[0].label).toBe("Code Reviewer");
  });

  it("maps recent files into searchable mention options", () => {
    const options = fileMentionOptions(["src/auth.ts", "src/api.ts"]);
    const matches = filterMentionOptions(options, {
      open: true,
      kind: "file",
      query: "auth",
      cursor: 0,
      anchor: 0,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].id).toBe("src/auth.ts");
  });

  it("exposes a single repo option", () => {
    const options = repoMentionOptions();
    expect(options).toHaveLength(1);
    expect(options[0].kind).toBe("repo");
  });
});
