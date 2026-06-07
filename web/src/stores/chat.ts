/**
 * Chat store — owns the streaming message log and the send() loop.
 *
 * State machine:
 *   idle → sending → streaming → idle
 *
 * The store wires the IPC `agent.message_chunk` event into a streaming
 * assistant bubble. Tool-call/tool-result events are appended as
 * additional message bubbles so the user can see intermediate steps.
 */

import { create } from "zustand";
import { ipc, IPCError, typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import {
  StreamEvent,
  type MessageChunkData,
  type ToolCallData,
  type ToolResultData,
} from "../types/ipc";
import type { Message, ContentPart } from "../types/ipc";
import { useSessionStore } from "./sessionStore";
import { trimArray, MAX_MESSAGES } from "../lib/eviction";

export type ChatStatus = "idle" | "sending" | "streaming" | "error";

export interface ChatState {
  messages: Message[];
  status: ChatStatus;
  error: string | null;
  agentReady: boolean;

  init: () => Promise<void>;
  send: (content: string | ContentPart[]) => Promise<void>;
  /** Add a user message to the chat log without triggering a backend call. */
  addLocalMessage: (content: string) => void;
  /** Load persisted messages for a session from the backend. */
  loadMessages: (sessionId: string) => Promise<void>;
  reset: () => void;
  cancel: () => Promise<void>;
}

let chunkUnsub: (() => void) | null = null;
let toolCallUnsub: (() => void) | null = null;
let toolResultUnsub: (() => void) | null = null;
let statusUnsub: (() => void) | null = null;

/** Monotonic counter to prevent stale loadMessages from overwriting state. */
let _loadSeq = 0;

/** Frontend stall watchdog — resets on every chunk, fires after 60 s idle. */
const STALL_TIMEOUT_MS = 60_000;
let _stallTimer: ReturnType<typeof setTimeout> | null = null;

function resetStallWatchdog() {
  if (_stallTimer) clearTimeout(_stallTimer);
  _stallTimer = setTimeout(() => {
    const s = useChat.getState();
    if (s.status === "streaming" || s.status === "sending") {
      useChat.setState({ status: "error", error: "Stream timed out — no response for 60 s" });
      toast.error("Stream timed out", "No data received for 60 seconds. The connection may have stalled.");
    }
    _stallTimer = null;
  }, STALL_TIMEOUT_MS);
}

function clearStallWatchdog() {
  if (_stallTimer) {
    clearTimeout(_stallTimer);
    _stallTimer = null;
  }
}

function ensureMessage(
  messages: Message[],
  patch: Partial<Message> & { id: string },
): Message[] {
  if (messages.some((m) => m.id === patch.id)) {
    return messages.map((m) => (m.id === patch.id ? { ...m, ...patch } : m));
  }
  return [
    ...messages,
    {
      role: "assistant",
      text: "",
      streaming: false,
      created_at: Date.now(),
      ...patch,
    } as Message,
  ];
}

export const useChat = create<ChatState>((set, get) => ({
  messages: [],
  status: "idle",
  error: null,
  agentReady: false,

  init: async () => {
    if (get().agentReady) return;
    await ipc.start();

    if (!chunkUnsub) {
      chunkUnsub = ipc.on<MessageChunkData>(StreamEvent.MessageChunk, (env) => {
        const data = env.data;
        if (!data) return;
        set((s) => {
          // The v0.3.0 thinking_count channel attaches a
          // ``metadata`` field to the final ``done=True`` chunk
          // (sometimes also the first chunk). Replace, don't
          // sum — the wire spec says it's a snapshot, not a
          // counter. We only assign when the field is present
          // so an earlier chunk's metadata isn't clobbered by a
          // later one that omits it.
          const metadata = data.metadata;
          const exists = s.messages.some((m) => m.id === data.message_id);
          const next = exists
            ? s.messages.map((m) =>
                m.id === data.message_id
                  ? {
                      ...m,
                      text: m.text + (data.delta || ""),
                      streaming: !data.done,
                      ...(metadata ? { metadata } : {}),
                    }
                  : m,
              )
            : ensureMessage(s.messages, {
                id: data.message_id,
                role: "assistant",
                text: data.delta || "",
                streaming: !data.done,
                created_at: Date.now(),
                ...(metadata ? { metadata } : {}),
              });
          return {
            messages: trimArray(next, MAX_MESSAGES),
            status: data.done ? "idle" : "streaming",
          };
        });
        // Stall watchdog: reset on every chunk, clear on done.
        if (data.done) {
          clearStallWatchdog();
        } else {
          resetStallWatchdog();
        }
      });
    }
    if (!toolCallUnsub) {
      toolCallUnsub = ipc.on<ToolCallData>(StreamEvent.ToolCall, (env) => {
        const data = env.data;
        if (!data) return;
        const id = `tc-${data.tool_call_id}`;
        set((s) => ({
          messages: trimArray(ensureMessage(s.messages, {
            id,
            role: "tool",
            text: `🔧 ${data.name}(${JSON.stringify(data.args)})`,
            tool_call_id: data.tool_call_id,
            tool_name: data.name,
            tool_args: data.args,
            created_at: Date.now(),
            streaming: false,
          }), MAX_MESSAGES),
          status: "streaming",
        }));
      });
    }
    if (!toolResultUnsub) {
      toolResultUnsub = ipc.on<ToolResultData>(StreamEvent.ToolResult, (env) => {
        const data = env.data;
        if (!data) return;
        const id = `tr-${data.tool_call_id}`;
        const text = data.error
          ? `✗ ${data.error}`
          : `✓ ${typeof data.result === "string" ? data.result : JSON.stringify(data.result)}`;
        set((s) => ({
          messages: trimArray(ensureMessage(s.messages, {
            id,
            role: "tool",
            text,
            tool_call_id: data.tool_call_id,
            created_at: Date.now(),
            streaming: false,
          }), MAX_MESSAGES),
        }));
      });
    }
    if (!statusUnsub) {
      statusUnsub = ipc.on(StreamEvent.AgentStatus, (env) => {
        const d = env.data as { status?: string; detail?: string } | undefined;
        if (!d) return;
        if (d.status === "error") {
          set({ status: "error", error: d.detail ?? "agent error" });
          toast.error("Agent error", d.detail);
        } else if (d.status === "idle") {
          set({ status: "idle" });
        }
      });
    }

    const ready = await ipc.ping();
    set({ agentReady: ready });
  },

  addLocalMessage: (content: string) => {
    const text = content.trim();
    if (!text) return;
    set((s) => ({
      messages: trimArray([
        ...s.messages,
        {
          id: `user-${Date.now()}`,
          role: "user",
          text,
          streaming: false,
          created_at: Date.now(),
        },
      ], MAX_MESSAGES),
    }));
  },

  send: async (content: string | ContentPart[]) => {
    // Normalize to wire format
    const isString = typeof content === "string";
    const text = isString ? (content as string).trim() : "";
    const parts = !isString ? (content as ContentPart[]) : undefined;

    // Build display text for the local user message bubble
    let displayText = text;
    if (parts) {
      const textParts = parts.filter((p) => p.type === "text").map((p) => p.text);
      const imgCount = parts.filter((p) => p.type === "image").length;
      displayText = textParts.join(" ") + (imgCount > 0 ? ` [${imgCount} image${imgCount > 1 ? "s" : ""}]` : "");
      displayText = displayText.trim();
    }

    if (!displayText && !parts?.length) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      text: displayText,
      streaming: false,
      created_at: Date.now(),
    };
    set((s) => ({
      messages: trimArray([...s.messages, userMessage], MAX_MESSAGES),
      status: "sending",
      error: null,
    }));
    resetStallWatchdog();
    const sessionId = useSessionStore.getState().currentSessionId;
    try {
      // Send string for pure text, list for multimodal
      const wireContent = parts ?? text;
      const result = await typedIPC.sendMessage({
        session_id: sessionId,
        content: wireContent,
      });
      if (result.session_id && !sessionId) {
        useSessionStore.getState().setCurrent(result.session_id);
      }
      // The HTTP response arrives *after* all WebSocket chunk events
      // have been processed (the agent streams chunks via WS, then
      // sends the JSON-RPC reply).  If the message was already
      // populated by chunk events, we must NOT overwrite the
      // accumulated text with "" — doing so would erase everything
      // the user just saw stream in.  Only create a fallback message
      // when no chunks were received (e.g. mock mode timing edge).
      set((s) => {
        const existing = s.messages.find((m) => m.id === result.message_id);
        if (existing) {
          // Already populated via WebSocket chunks — finalise status.
          return { status: existing.streaming ? "streaming" : "idle" };
        }
        // No chunks received — create message from the full reply text.
        return {
          messages: trimArray(ensureMessage(s.messages, {
            id: result.message_id,
            role: "assistant",
            text: result.text || "",
            streaming: false,
            created_at: Date.now(),
          }), MAX_MESSAGES),
          status: "idle",
        };
      });
    } catch (err) {
      const message =
        err instanceof IPCError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      set((s) => ({
        status: "error",
        error: message,
        messages: trimArray([
          ...s.messages,
          {
            id: `err-${Date.now()}`,
            role: "system",
            text: `Error: ${message}`,
            streaming: false,
            created_at: Date.now(),
          },
        ], MAX_MESSAGES),
      }));
      toast.error("Send failed", message);
    }
  },

  cancel: async () => {
    clearStallWatchdog();
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    try {
      await typedIPC.cancelAgent(sid);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Cancel failed", message);
    }
    set({ status: "idle" });
  },

  loadMessages: async (sessionId: string) => {
    if (!sessionId) return;
    // Bump the sequence counter so any in-flight load from a
    // previous session is silently discarded.
    const seq = ++_loadSeq;
    try {
      const result = await typedIPC.listMessages(sessionId);
      // Stale response — the user has already switched away.
      if (seq !== _loadSeq) return;
      // Convert backend rows (content → text, add streaming: false)
      const msgs: Message[] = (result.messages ?? []).map((m: any) => ({
        id: m.id,
        role: m.role,
        text: m.text ?? m.content ?? "",
        streaming: false,
        created_at: m.created_at,
        metadata: m.metadata,
        tool_call_id: m.tool_call_id,
      }));
      set({ messages: trimArray(msgs, MAX_MESSAGES), status: "idle", error: null });
    } catch (err) {
      // Stale — skip error toast for abandoned requests.
      if (seq !== _loadSeq) return;
      // If loading fails (e.g. session has no messages yet), just clear.
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load messages", message);
      set({ messages: [], status: "idle", error: null });
    }
  },

  reset: () => {
    clearStallWatchdog();
    set({ messages: [], status: "idle", error: null });
  },
}));

export type { Message };
