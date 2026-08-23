/**
 * Session-scope guards for broadcast-fed stores (v1.2.0 audit cuts 1/2/4).
 *
 * The backend fans every WebSocket event out to every connected client —
 * skill invocations, background prompts and runs from other tabs all ride
 * the same channels. These tests pin the three store-level guards that
 * keep foreign traffic out of the currently open session:
 *
 *   - chat: message_chunk / tool_call / status events stamped with a
 *     foreign session_id must not touch the visible conversation
 *   - teamRunStore: progress events arrive as envelopes; the payload
 *     lives in ``env.data`` (casting the envelope itself produced ghost
 *     entries whose fields were all undefined)
 *   - runStore: run.created / run.completed for other sessions are
 *     dropped instead of appending ghost timeline entries
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";

// The ipc module is replaced with a tiny in-memory event bus so tests can
// fire server-pushed events directly at the store handlers. Both module
// specifiers must be mocked — stores import either the barrel ("../ipc")
// or the client module directly ("../ipc/client", e.g. teamRunStore).
const bus = vi.hoisted(() => {
  const handlers = new Map<string, Array<(env: { data?: unknown }) => void>>();
  return { handlers };
});

function makeMockIpcModule() {
  return {
    ipc: {
      start: vi.fn().mockResolvedValue(undefined),
      ping: vi.fn().mockResolvedValue(true),
      on: vi.fn((event: string, handler: (env: { data?: unknown }) => void) => {
        const list = bus.handlers.get(event) ?? [];
        list.push(handler);
        bus.handlers.set(event, list);
        return () => {
          bus.handlers.set(event, (bus.handlers.get(event) ?? []).filter((h) => h !== handler));
        };
      }),
    },
    IPCError: class extends Error {},
    typedIPC: {},
  };
}

vi.mock("../../ipc", () => makeMockIpcModule());
vi.mock("../../ipc/client", () => makeMockIpcModule());

import { useChat, disposeChatSubscriptions } from "../chat";
import { useSessionStore } from "../sessionStore";
import { useRunTimelineStore } from "../runStore";
import { useTeamRunStore, initTeamRunListener } from "../teamRunStore";
import { StreamEvent } from "../../types/ipc";

/** Fire a server-push event at every registered handler. */
function fire(event: string, data: unknown): void {
  for (const handler of bus.handlers.get(event) ?? []) {
    handler({ data });
  }
}

function openSession(id: string): void {
  useSessionStore.setState({ currentSessionId: id });
}

beforeAll(() => {
  // runStore / teamRunStore subscribe once and stay armed for the file.
  useRunTimelineStore.getState().init();
  initTeamRunListener();
});

beforeEach(async () => {
  vi.clearAllMocks();
  // Re-arm the chat subscriptions for each test: dispose tears the old
  // handlers out of the bus and reset() leaves agentReady set (it only
  // clears message state), so the flag must be flipped back for init()
  // to re-subscribe instead of returning early.
  disposeChatSubscriptions();
  useChat.getState().reset();
  useChat.setState({ agentReady: false });
  await useChat.getState().init();
  useRunTimelineStore.setState({ runs: {}, order: [], initialized: true, loading: false, error: null });
  useTeamRunStore.setState({ runs: [] });
  useSessionStore.setState({ sessions: [], currentSessionId: null });
});

describe("chat store session guard (v1.2.0 cut 1)", () => {
  it("ignores message_chunk events from a foreign session", () => {
    openSession("s-now");
    fire(StreamEvent.MessageChunk, {
      session_id: "s-other",
      message_id: "m-foreign",
      delta: "background noise",
      done: true,
    });
    expect(useChat.getState().messages).toHaveLength(0);

    fire(StreamEvent.MessageChunk, {
      session_id: "s-now",
      message_id: "m-own",
      delta: "visible reply",
      done: true,
    });
    const msgs = useChat.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe("m-own");
    expect(msgs[0].text).toBe("visible reply");
  });

  it("ignores tool_call events from a foreign session", () => {
    openSession("s-now");
    fire(StreamEvent.ToolCall, {
      session_id: "s-other",
      tool_call_id: "tc-foreign",
      name: "write_file",
      args: { path: "/tmp/x" },
    });
    expect(useChat.getState().messages).toHaveLength(0);

    fire(StreamEvent.ToolCall, {
      session_id: "s-now",
      tool_call_id: "tc-own",
      name: "read_file",
      args: { path: "/tmp/y" },
    });
    expect(useChat.getState().messages.map((m) => m.id)).toContain("tc-tc-own");
  });

  it("ignores agent.status events from a foreign session", () => {
    openSession("s-now");
    fire(StreamEvent.AgentStatus, {
      session_id: "s-other",
      status: "error",
      detail: "background run exploded",
    });
    expect(useChat.getState().error).toBeNull();

    fire(StreamEvent.AgentStatus, {
      session_id: "s-now",
      status: "error",
      detail: "own run failed",
    });
    expect(useChat.getState().error).toContain("own run failed");
  });

  it("ignores events entirely while no session is open", () => {
    openSession("s-now");
    useSessionStore.setState({ currentSessionId: null });
    fire(StreamEvent.MessageChunk, {
      session_id: "s-now",
      message_id: "m-orphan",
      delta: "nobody is listening",
      done: true,
    });
    expect(useChat.getState().messages).toHaveLength(0);
  });
});

describe("teamRunStore envelope unwrap (v1.2.0 cut 2)", () => {
  it("reads the progress payload from env.data, not the envelope", () => {
    fire(StreamEvent.TeamProgress, {
      team_name: "review",
      task_id: "tr-1",
      status: "started",
      progress: 0.1,
      agents_total: 2,
    });
    const runs = useTeamRunStore.getState().runs;
    expect(runs).toHaveLength(1);
    expect(runs[0].task_id).toBe("tr-1");
    expect(runs[0].team_name).toBe("review");
    expect(runs[0].status).toBe("started");
    expect(runs[0].progress).toBeCloseTo(0.1);
  });

  it("updates an existing run instead of appending a duplicate", () => {
    fire(StreamEvent.TeamProgress, {
      team_name: "review",
      task_id: "tr-1",
      status: "started",
      progress: 0,
      agents_total: 2,
    });
    fire(StreamEvent.TeamProgress, {
      team_name: "review",
      task_id: "tr-1",
      status: "completed",
      progress: 1,
      agents_completed: 2,
    });
    const runs = useTeamRunStore.getState().runs;
    expect(runs).toHaveLength(1);
    expect(runs[0].status).toBe("completed");
    expect(runs[0].progress).toBe(1);
    expect(runs[0].agents_completed).toBe(2);
  });

  it("skips events whose envelope carries no data", () => {
    fire(StreamEvent.TeamProgress, undefined);
    expect(useTeamRunStore.getState().runs).toHaveLength(0);
  });
});

describe("runStore session filter (v1.2.0 cut 4)", () => {
  it("drops run.created for a foreign session", () => {
    openSession("s-now");
    fire(StreamEvent.RunCreated, {
      run: {
        id: "run-foreign",
        session_id: "s-other",
        mode: "chat",
        status: "running",
        title: "other tab's run",
        created_at: "2026-08-23T00:00:00Z",
      },
    });
    expect(useRunTimelineStore.getState().order).toHaveLength(0);

    fire(StreamEvent.RunCreated, {
      run: {
        id: "run-own",
        session_id: "s-now",
        mode: "chat",
        status: "running",
        title: "my run",
        created_at: "2026-08-23T00:00:01Z",
      },
    });
    expect(useRunTimelineStore.getState().order).toContain("run-own");
  });

  it("drops orphan run.completed from a foreign session but updates known runs", () => {
    openSession("s-now");
    fire(StreamEvent.RunCreated, {
      run: {
        id: "run-own",
        session_id: "s-now",
        mode: "chat",
        status: "running",
        title: "my run",
        created_at: "2026-08-23T00:00:00Z",
      },
    });

    // A foreign completion we never saw created — ghost entry, dropped.
    fire(StreamEvent.RunCompleted, {
      run: {
        id: "run-ghost",
        session_id: "s-other",
        mode: "chat",
        status: "completed",
        title: "ghost",
        created_at: "2026-08-23T00:00:02Z",
      },
    });
    expect(useRunTimelineStore.getState().runs["run-ghost"]).toBeUndefined();

    // Completing a run we did see — updates in place.
    fire(StreamEvent.RunCompleted, {
      run: {
        id: "run-own",
        session_id: "s-now",
        mode: "chat",
        status: "completed",
        title: "my run",
        created_at: "2026-08-23T00:00:00Z",
      },
    });
    expect(useRunTimelineStore.getState().runs["run-own"]?.status).toBe("completed");
  });
});
