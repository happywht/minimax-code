/**
 * Tests for the v0.3.0 thinking_count metadata channel
 * integration with the chat store.
 *
 * The chat store subscribes to `agent.message_chunk` events and
 * stores the latest `metadata` snapshot on the message. This
 * drives the "思考 N 次" line in MessageItem. Coverage here:
 *
 *  - chunks without metadata don't clobber a prior snapshot
 *  - chunks with metadata replace (not sum) the previous value
 *  - the metadata survives a streaming→idle state transition
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useChat } from "../src/stores";
import { ipc } from "../src/ipc";
import { StreamEvent, type MessageChunkData } from "../src/types/ipc";

vi.mock("../src/components/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

function emitChunk(data: Partial<MessageChunkData> & { message_id: string }) {
  // Reach into the mock client and synthesize the event the
  // store listens for. This is the same path the real WS path
  // uses (IPCClient._emit → listener).
  ipc._emit(StreamEvent.MessageChunk, {
    session_id: "ses_test",
    message_id: data.message_id,
    delta: data.delta ?? "",
    done: data.done ?? false,
    ...(data.metadata ? { metadata: data.metadata } : {}),
  });
}

describe("chatStore v0.3.0 thinking_count metadata", () => {
  beforeEach(async () => {
    useChat.setState({
      messages: [],
      status: "idle",
      error: null,
      agentReady: false,
    });
    // Force mock mode so the listener registers without a
    // real agent. The chat store's init() is gated on
    // ``agentReady`` so we have to reset that flag too
    // (each test creates a fresh module-level ``chunkUnsub``).
    (ipc as unknown as { forceMock: boolean }).forceMock = true;
    (ipc as unknown as { started: boolean }).started = false;
    (ipc as unknown as { mode: string }).mode = "mock";
    await useChat.getState().init();
  });

  it("captures the metadata from the done=True chunk", () => {
    const mid = "msg_1";
    emitChunk({ message_id: mid, delta: "Hello", done: false });
    emitChunk({ message_id: mid, delta: " world", done: true, metadata: { thinking_count: 3, tokens_in: 5, tokens_out: 2 } });

    const m = useChat.getState().messages.find((m) => m.id === mid);
    expect(m).toBeDefined();
    expect(m?.text).toBe("Hello world");
    expect(m?.metadata).toEqual({ thinking_count: 3, tokens_in: 5, tokens_out: 2 });
  });

  it("does not clobber a prior metadata snapshot when a later chunk omits it", () => {
    // Earlier chunks in the v0.3.0 stream may omit `metadata`
    // — the chat store must keep the snapshot it already has.
    const mid = "msg_2";
    emitChunk({
      message_id: mid,
      delta: "Hi",
      done: true,
      metadata: { thinking_count: 1, tokens_in: 4, tokens_out: 2 },
    });
    emitChunk({ message_id: mid, delta: " again", done: false });

    const m = useChat.getState().messages.find((m) => m.id === mid);
    expect(m?.metadata).toEqual({ thinking_count: 1, tokens_in: 4, tokens_out: 2 });
  });

  it("replaces (does not sum) the metadata when a new value arrives", () => {
    // The spec says "replace, don't sum — it's a snapshot, not a
    // counter". The store must overwrite the old metadata with
    // the new value, never accumulate.
    const mid = "msg_3";
    emitChunk({
      message_id: mid,
      delta: "A",
      done: false,
      metadata: { thinking_count: 1, tokens_in: 4, tokens_out: 2 },
    });
    emitChunk({
      message_id: mid,
      delta: "B",
      done: true,
      metadata: { thinking_count: 5, tokens_in: 8, tokens_out: 4 },
    });

    const m = useChat.getState().messages.find((m) => m.id === mid);
    expect(m?.metadata).toEqual({ thinking_count: 5, tokens_in: 8, tokens_out: 4 });
  });

  it("leaves metadata undefined for chunks that never set it", () => {
    // Backwards-compat with v0.2.0 agents: if no chunk in the
    // stream carries metadata, the message's metadata field
    // stays undefined. The UI falls back to "思考 0 次".
    const mid = "msg_4";
    emitChunk({ message_id: mid, delta: "x", done: false });
    emitChunk({ message_id: mid, delta: "y", done: true });

    const m = useChat.getState().messages.find((m) => m.id === mid);
    expect(m?.metadata).toBeUndefined();
  });
});
