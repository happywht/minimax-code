/**
 * Tests for the ask_user slice of the chat store.
 *
 * Visibility lifecycle:
 * - `agent.ask_user` event stores the questionnaire;
 * - submitAskUser posts `agent.answer_user` and clears the card on
 *   accept or explicit reject (ok:false), but keeps it up on transport
 *   errors so the user can retry within the backend timeout window;
 * - an ask_user tool_result event clears the card (timeout / cancel
 *   path — those never route through submitAskUser);
 * - reset() drops the card with everything else.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { useChat } from "../chat";
import { typedIPC } from "../../ipc";
import { StreamEvent } from "../../types/ipc";

// ── Capture event handlers registered via ipc.on ──────────────────
const handlers = new Map<string, (env: any) => void>();

function mockIpcOn(event: string, handler: (env: any) => void): () => void {
  handlers.set(event, handler);
  return () => handlers.delete(event);
}

const toastErrorSpy = vi.fn();

vi.mock("../../ipc", () => ({
  ipc: {
    start: vi.fn().mockResolvedValue(undefined),
    on: vi.fn((event: string, handler: (env: any) => void) => mockIpcOn(event, handler)),
    ping: vi.fn().mockResolvedValue(true),
  },
  IPCError: class extends Error {},
  typedIPC: {
    sendMessage: vi.fn(),
    cancelAgent: vi.fn().mockResolvedValue({ ok: true }),
    answerUser: vi.fn(),
  },
}));

vi.mock("../../components/layout/ErrorBoundary", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorSpy(...args),
    info: vi.fn(),
  },
}));

vi.mock("../sessionStore", () => ({
  useSessionStore: {
    getState: vi.fn().mockReturnValue({ currentSessionId: "test-session", sessions: [] }),
    setState: vi.fn(),
  },
}));

const QUESTIONS = [
  {
    question: "Which database?",
    header: "Database",
    options: [
      { label: "SQLite", description: "embedded" },
      { label: "PostgreSQL", description: "server" },
    ],
    multiSelect: false,
  },
  {
    question: "Which features?",
    header: "Features",
    options: [
      { label: "Auth", description: "" },
      { label: "Audit", description: "" },
    ],
    multiSelect: true,
  },
];

function emitAskUser(requestId = "ask_1"): void {
  handlers.get(StreamEvent.AskUser)?.({
    data: {
      request_id: requestId,
      session_id: "test-session",
      questions: QUESTIONS,
      timeout_s: 600,
    },
  });
}

function emitAskUserToolResult(): void {
  handlers.get(StreamEvent.ToolResult)?.({
    data: {
      session_id: "test-session",
      tool_call_id: "tc_1",
      name: "ask_user",
      result: "The user answered",
      message_id: "m1",
    },
  });
}

describe("ask_user slice", () => {
  beforeAll(async () => {
    await useChat.getState().init();
  });

  beforeEach(() => {
    toastErrorSpy.mockClear();
    vi.mocked(typedIPC.answerUser).mockReset();
    useChat.getState().reset();
  });

  it("stores the questionnaire on agent.ask_user event", () => {
    emitAskUser();
    const askUser = useChat.getState().askUser;
    expect(askUser).not.toBeNull();
    expect(askUser?.request_id).toBe("ask_1");
    expect(askUser?.questions).toHaveLength(2);
  });

  it("a later questionnaire replaces the previous one", () => {
    emitAskUser("ask_1");
    emitAskUser("ask_2");
    expect(useChat.getState().askUser?.request_id).toBe("ask_2");
  });

  it("clears the card when the ask_user tool_result arrives", () => {
    emitAskUser();
    expect(useChat.getState().askUser).not.toBeNull();
    emitAskUserToolResult();
    expect(useChat.getState().askUser).toBeNull();
  });

  it("non-ask_user tool_result events leave the card alone", () => {
    emitAskUser();
    handlers.get(StreamEvent.ToolResult)?.({
      data: {
        session_id: "test-session",
        tool_call_id: "tc_2",
        name: "search",
        result: "ok",
        message_id: "m1",
      },
    });
    expect(useChat.getState().askUser).not.toBeNull();
  });

  it("submitAskUser posts positionally aligned answers and clears on success", async () => {
    vi.mocked(typedIPC.answerUser).mockResolvedValue({ ok: true, request_id: "ask_1" });
    emitAskUser();
    await useChat.getState().submitAskUser(["SQLite", ["Auth", "Audit"]]);

    expect(typedIPC.answerUser).toHaveBeenCalledWith("ask_1", ["SQLite", ["Auth", "Audit"]]);
    expect(useChat.getState().askUser).toBeNull();
    expect(toastErrorSpy).not.toHaveBeenCalled();
  });

  it("submitAskUser clears the card when the backend rejects (expired id)", async () => {
    vi.mocked(typedIPC.answerUser).mockResolvedValue({
      ok: false,
      error: "unknown or expired request_id",
    });
    emitAskUser();
    await useChat.getState().submitAskUser(["SQLite", ["Auth"]]);

    expect(useChat.getState().askUser).toBeNull();
    expect(toastErrorSpy).toHaveBeenCalledTimes(1);
  });

  it("submitAskUser keeps the card on transport errors for retry", async () => {
    vi.mocked(typedIPC.answerUser).mockRejectedValue(new Error("network down"));
    emitAskUser();
    await useChat.getState().submitAskUser(["SQLite", ["Auth"]]);

    expect(useChat.getState().askUser).not.toBeNull();
    expect(toastErrorSpy).toHaveBeenCalledTimes(1);
  });

  it("skipAskUser answers every question with the skip marker", async () => {
    vi.mocked(typedIPC.answerUser).mockResolvedValue({ ok: true, request_id: "ask_1" });
    emitAskUser();
    await useChat.getState().skipAskUser();

    expect(typedIPC.answerUser).toHaveBeenCalledWith(
      "ask_1",
      ["(skipped by user)", "(skipped by user)"],
    );
    expect(useChat.getState().askUser).toBeNull();
  });

  it("submit/skip are no-ops without a pending questionnaire", async () => {
    await useChat.getState().submitAskUser(["x"]);
    await useChat.getState().skipAskUser();
    expect(typedIPC.answerUser).not.toHaveBeenCalled();
  });

  it("reset drops the pending questionnaire", () => {
    emitAskUser();
    useChat.getState().reset();
    expect(useChat.getState().askUser).toBeNull();
  });
});
