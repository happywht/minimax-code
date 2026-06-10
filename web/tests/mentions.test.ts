import { describe, expect, it } from "vitest";
import {
  agentMentionOptions,
  detectMentionToken,
  filterMentionOptions,
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
});
