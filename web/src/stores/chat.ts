/**
 * Chat store — owns the streaming message log and the send() loop.
 *
 * State machine:
 *   idle → sending → streaming → idle
 *   idle → sending → error
 *   streaming → cancelling → idle
 *
 * The store wires the IPC `agent.message_chunk` event into a streaming
 * assistant bubble. Tool-call/tool-result events are appended as
 * additional message bubbles so the user can see intermediate steps.
 */

import { create } from "zustand";
import { ipc, IPCError, typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import {
  StreamEvent,
  type MessageChunkData,
  type ToolCallData,
  type ToolResultData,
} from "../types/ipc";
import type { Message, ContentPart } from "../types/ipc";
import { useSessionStore } from "./sessionStore";
import { trimArray, MAX_MESSAGES } from "../lib/eviction";
import { DEFAULT_SESSION_TITLE } from "../lib/defaultTitles";
import { strings } from "../ui/strings";

export type ChatStatus = "idle" | "sending" | "streaming" | "error" | "cancelling";

export interface ChatState {
  messages: Message[];
  status: ChatStatus;
  error: string | null;
  agentReady: boolean;
  /** True while loadMessages is fetching history for the current session. */
  loadingMessages: boolean;

  init: () => Promise<void>;
  send: (content: string | ContentPart[]) => Promise<void>;
  /** Add a user message to the chat log without triggering a backend call. */
  addLocalMessage: (content: string) => void;
  /** Load persisted messages for a session from the backend. */
  loadMessages: (sessionId: string) => Promise<void>;
  reset: () => void;
  cancel: () => Promise<void>;
  retryMessage: (messageId: string) => Promise<void>;
  updateMessage: (messageId: string, text: string) => Promise<void>;
  deleteMessage: (messageId: string) => Promise<void>;
}

let chunkUnsub: (() => void) | null = null;
let toolCallUnsub: (() => void) | null = null;
let toolResultUnsub: (() => void) | null = null;
let statusUnsub: (() => void) | null = null;
let pendingAssistantId: string | null = null;

// HMR cleanup — tear down WS listeners when this module is hot-replaced
// so stale subscriptions don't accumulate and cause duplicate event handling.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    chunkUnsub?.();
    toolCallUnsub?.();
    toolResultUnsub?.();
    statusUnsub?.();
  });
}

/** Monotonic counter to prevent stale loadMessages from overwriting state. */
let _loadSeq = 0;

/** Frontend inactivity notice. Only the backend may terminate a run. */
const STALL_TIMEOUT_MS = 60_000;
/**
 * Backend tool dispatch defaults to 120 s. While a tool is actively
 * running, allow a wider silence window so long shell/test/search work
 * does not get misreported as a dead chat stream.
 */
const TOOL_STALL_TIMEOUT_MS = 180_000;
let _stallTimer: ReturnType<typeof setTimeout> | null = null;
const activeToolCalls = new Set<string>();
const activeToolNames = new Map<string, string>();

function resetStallWatchdog(timeoutMs = STALL_TIMEOUT_MS) {
  if (_stallTimer) clearTimeout(_stallTimer);
  _stallTimer = setTimeout(() => {
    _stallTimer = null;
    const s = useChat.getState();
    if (s.status === "streaming" || s.status === "sending") {
      const seconds = Math.round(timeoutMs / 1000);
      const detail =
        activeToolCalls.size > 0
          ? `No tool progress received for ${seconds} seconds. The current tool may have stalled.`
          : `No data received for ${seconds} seconds. The connection may have stalled.`;
      toast.info(strings.toasts.agentBusy, detail);
      resetStallWatchdogForCurrentActivity();
    }
  }, timeoutMs);
}

function resetStallWatchdogForCurrentActivity() {
  resetStallWatchdog(activeToolCalls.size > 0 ? TOOL_STALL_TIMEOUT_MS : STALL_TIMEOUT_MS);
}

function clearStallWatchdog() {
  if (_stallTimer) {
    clearTimeout(_stallTimer);
    _stallTimer = null;
  }
  activeToolCalls.clear();
  activeToolNames.clear();
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
      status: patch.streaming ? "streaming" : "completed",
      created_at: Date.now(),
      ...patch,
    } as Message,
  ];
}

function applyAssistantChunk(
  messages: Message[],
  data: MessageChunkData,
): Message[] {
  const metadata = data.metadata;
  const delta = data.delta || "";
  const nextStatus = data.done ? "completed" : "streaming";
  const existing = messages.find((m) => m.id === data.message_id);

  if (existing) {
    return messages.map((m) =>
      m.id === data.message_id
        ? {
            ...m,
            text: m.text + delta,
            streaming: !data.done,
            status: nextStatus,
            ...(metadata ? { metadata } : {}),
          }
        : m,
    );
  }

  const pendingId = pendingAssistantId;
  if (data.done && !delta && pendingId) {
    const pending = messages.find((m) => m.id === pendingId);
    if (pending && pending.role === "assistant" && !pending.text) {
      return messages.filter((m) => m.id !== pendingId);
    }
  }

  if (pendingId && messages.some((m) => m.id === pendingId)) {
    return messages.map((m) =>
      m.id === pendingId
        ? {
            ...m,
            id: data.message_id,
            text: m.text + delta,
            streaming: !data.done,
            status: nextStatus,
            ...(metadata ? { metadata } : {}),
          }
        : m,
    );
  }

  return ensureMessage(messages, {
    id: data.message_id,
    role: "assistant",
    text: delta,
    streaming: !data.done,
    status: nextStatus,
    created_at: Date.now(),
    ...(metadata ? { metadata } : {}),
  });
}

export const useChat = create<ChatState>((set, get) => ({
  messages: [],
  status: "idle",
  error: null,
  agentReady: false,
  loadingMessages: false,

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
          const next = applyAssistantChunk(s.messages, data);
          return {
            messages: trimArray(next, MAX_MESSAGES),
            status: data.done ? "idle" : "streaming",
          };
        });
        // Stall watchdog: reset on every chunk, clear on done.
        if (data.done) {
          pendingAssistantId = null;
          clearStallWatchdog();
        } else {
          resetStallWatchdogForCurrentActivity();
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
        // Tool execution is activity. It can legitimately run longer
        // than the normal 60 s stream silence window, so arm the wider
        // tool watchdog until a matching tool_result arrives.
        activeToolCalls.add(data.tool_call_id);
        activeToolNames.set(data.tool_call_id, data.name);
        resetStallWatchdogForCurrentActivity();
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
        const toolName = data.name ?? activeToolNames.get(data.tool_call_id);
        set((s) => ({
          messages: trimArray(ensureMessage(s.messages, {
            id,
            role: "tool",
            text,
            tool_call_id: data.tool_call_id,
            tool_name: toolName,
            parent_id: `tc-${data.tool_call_id}`,
            created_at: Date.now(),
            streaming: false,
          }), MAX_MESSAGES),
        }));
        // Tool result is activity. Once all active tools have returned,
        // go back to the normal stream watchdog window.
        activeToolCalls.delete(data.tool_call_id);
        activeToolNames.delete(data.tool_call_id);
        resetStallWatchdogForCurrentActivity();
      });
    }
    if (!statusUnsub) {
      statusUnsub = ipc.on(StreamEvent.AgentStatus, (env) => {
        const d = env.data as { status?: string; detail?: string } | undefined;
        if (!d) return;
        if (d.status === "error") {
          const detail = d.detail ?? "Agent run failed";
          set((s) => ({
            status: "error",
            error: detail,
            messages: s.messages.map((message) =>
              message.role === "assistant" && message.streaming
                ? {
                    ...message,
                    text: message.text || detail,
                    streaming: false,
                    status: "failed",
                    error: detail,
                  }
                : message,
            ),
          }));
          pendingAssistantId = null;
          toast.error(strings.toasts.agentError, detail);
          clearStallWatchdog();
        } else if (d.status === "idle" || d.status === "done" || d.status === "max_iterations") {
          set({ status: "idle" });
          clearStallWatchdog();
        } else {
          resetStallWatchdogForCurrentActivity();
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
          status: "completed",
          created_at: Date.now(),
        },
      ], MAX_MESSAGES),
    }));
  },

  send: async (content: string | ContentPart[]) => {
    // Concurrency guard — prevent double-send from rapid clicks or
    // multiple call sites.  The UI disables the button but the store
    // is the source of truth.
    if (get().status === "sending" || get().status === "streaming") return;

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

    activeToolCalls.clear();
    activeToolNames.clear();
    set({ status: "sending", error: null });
    let sessionId = useSessionStore.getState().currentSessionId;
    try {
      if (!sessionId) {
        const title =
          displayText.replace(/\s+/g, " ").trim().slice(0, 60) || DEFAULT_SESSION_TITLE;
        sessionId = await useSessionStore.getState().create(title);
      } else {
        const sessions = useSessionStore.getState();
        const current = sessions.sessions.find((session) => session.id === sessionId);
        if (current?.title === DEFAULT_SESSION_TITLE && get().messages.length === 0) {
          const title =
            displayText.replace(/\s+/g, " ").trim().slice(0, 60) || DEFAULT_SESSION_TITLE;
          await sessions.rename(sessionId, title);
        }
      }

      const userMessage: Message = {
        id: `user-${Date.now()}`,
        role: "user",
        text: displayText,
        streaming: false,
        status: "completed",
        created_at: Date.now(),
      };
      const assistantPlaceholderId = `assistant-pending-${Date.now()}`;
      pendingAssistantId = assistantPlaceholderId;
      const assistantPlaceholder: Message = {
        id: assistantPlaceholderId,
        role: "assistant",
        text: "",
        streaming: true,
        status: "queued",
        retry_content: parts ? displayText : text,
        created_at: Date.now(),
      };
      set((s) => ({
        messages: trimArray([...s.messages, userMessage, assistantPlaceholder], MAX_MESSAGES),
      }));
      resetStallWatchdogForCurrentActivity();
      // Send string for pure text, list for multimodal
      const wireContent = parts ?? text;
      const result = await typedIPC.sendMessage({
        session_id: sessionId,
        content: wireContent,
      });
      if (result.session_id && result.session_id !== sessionId) {
        const sessions = useSessionStore.getState();
        sessions.setCurrent(result.session_id, false);
        await sessions.refresh({ loadCurrent: false });
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
          return {
            messages: existing.streaming
              ? s.messages
              : s.messages.map((m) =>
                  m.id === result.message_id ? { ...m, status: "completed", streaming: false } : m,
                ),
            status: existing.streaming ? "streaming" : "idle",
          };
        }
        const pending = pendingAssistantId;
        if (pending && s.messages.some((m) => m.id === pending)) {
          return {
            messages: trimArray(s.messages.map((m) =>
              m.id === pending
                ? {
                    ...m,
                    id: result.message_id,
                    text: result.text || "",
                    streaming: false,
                    status: "completed",
                  }
                : m,
            ), MAX_MESSAGES),
            status: "idle",
          };
        }
        // No chunks received — create message from the full reply text.
        return {
          messages: trimArray(ensureMessage(s.messages, {
            id: result.message_id,
            role: "assistant",
            text: result.text || "",
            streaming: false,
            status: "completed",
            created_at: Date.now(),
          }), MAX_MESSAGES),
          status: "idle",
        };
      });
      pendingAssistantId = null;
    } catch (err) {
      const message =
        err instanceof IPCError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      const isTransportTimeout = message.includes("request timed out");

      set((s) => {
        // A client/proxy timeout does not prove the backend run ended.
        // Keep the stream active and let WebSocket status/done events
        // provide the authoritative terminal state.
        if (isTransportTimeout) {
          return s;
        }
        // The WebSocket error event may have already marked the active
        // assistant message as failed before the HTTP error arrives.
        if (
          s.status === "error" &&
          s.messages.some((m) => m.role === "assistant" && m.status === "failed")
        ) {
          return s;
        }
        const pending = pendingAssistantId;
        if (pending && s.messages.some((m) => m.id === pending)) {
          pendingAssistantId = null;
          activeToolCalls.clear();
          activeToolNames.clear();
          return {
            status: "error",
            error: message,
            messages: s.messages.map((m) =>
              m.id === pending
                ? {
                    ...m,
                    text: message,
                    streaming: false,
                    status: "failed",
                    error: message,
                    retry_content: parts ? displayText : text,
                  }
                : m,
            ),
          };
        }
        pendingAssistantId = null;
        activeToolCalls.clear();
        activeToolNames.clear();
        return {
          status: "error",
          error: message,
          messages: trimArray([
            ...s.messages,
            {
              id: `err-${Date.now()}`,
              role: "system",
              text: `Error: ${message}`,
              streaming: false,
              status: "failed",
              error: message,
              retry_content: parts ? displayText : text,
              created_at: Date.now(),
            },
          ], MAX_MESSAGES),
        };
      });
      if (isTransportTimeout) {
        toast.info(strings.toasts.agentBusy, strings.toasts.agentBusyDetail);
      } else {
        toast.error(strings.toasts.sendFailed, message);
      }
    }
  },

  cancel: async () => {
    clearStallWatchdog();
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    set((s) => ({
      status: "cancelling",
      messages: s.messages.map((m) =>
        m.role === "assistant" && (m.streaming || m.status === "queued" || m.status === "streaming")
          ? { ...m, status: "cancelling" }
          : m,
      ),
    }));
    try {
      await typedIPC.cancelAgent(sid);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.cancelFailed, message);
    }
    pendingAssistantId = null;
    set((s) => ({
      status: "idle",
      messages: s.messages.map((m) =>
        m.status === "cancelling"
          ? {
              ...m,
              text: m.text || "Stopped.",
              streaming: false,
              status: "cancelled",
            }
          : m,
      ),
    }));
  },

  retryMessage: async (messageId: string) => {
    const state = get();
    const idx = state.messages.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    const failed = state.messages[idx];
    const previousUser = [...state.messages.slice(0, idx)]
      .reverse()
      .find((m) => m.role === "user");
    const content = failed.retry_content ?? previousUser?.text ?? "";
    if (!content.trim()) return;
    set((s) => ({
      messages: s.messages.filter((m) => m.id !== messageId),
      status: "idle",
      error: null,
    }));
    await get().send(content);
  },

  updateMessage: async (messageId: string, text: string) => {
    try {
      await typedIPC.updateMessage({ message_id: messageId, content: text });
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === messageId ? { ...m, text, retry_content: text } : m
        ),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messageUpdateFailed, message);
    }
  },

  deleteMessage: async (messageId: string) => {
    try {
      await typedIPC.deleteMessage({ message_id: messageId });
      set((s) => ({
        messages: s.messages.filter((m) => m.id !== messageId),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messageDeleteFailed, message);
    }
  },

  loadMessages: async (sessionId: string) => {
    if (!sessionId) return;
    // Bump the sequence counter so any in-flight load from a
    // previous session is silently discarded.
    const seq = ++_loadSeq;
    set({ loadingMessages: true });
    try {
      const result = await typedIPC.listMessages(sessionId);
      // Stale response — the user has already switched away.
      if (seq !== _loadSeq) return;
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const activeStatus = get().status;
      if (
        currentSessionId === sessionId &&
        (activeStatus === "sending" || activeStatus === "streaming")
      ) {
        set({ loadingMessages: false });
        return;
      }
      // Convert backend rows (content → text, add streaming: false)
      const msgs: Message[] = (result.messages ?? []).map((m) => ({
        id: m.id,
        role: m.role,
        text: m.text ?? m.content ?? "",
        streaming: false,
        status: m.status ?? "completed",
        created_at: m.created_at,
        metadata: m.metadata,
        tool_call_id: m.tool_call_id,
        tool_name: m.tool_name,
        tool_args: m.tool_args,
      }));
      set({ messages: trimArray(msgs, MAX_MESSAGES), status: "idle", error: null, loadingMessages: false });
    } catch (err) {
      // Stale — skip error toast for abandoned requests.
      if (seq !== _loadSeq) return;
      // If loading fails (e.g. session has no messages yet), just clear.
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.messagesLoadFailed, message);
      set({ messages: [], status: "idle", error: null, loadingMessages: false });
    }
  },

  reset: () => {
    ++_loadSeq;
    clearStallWatchdog();
    pendingAssistantId = null;
    set({ messages: [], status: "idle", error: null, loadingMessages: false });
  },
}));

export type { Message };
