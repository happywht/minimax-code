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
import type { Message } from "../types/ipc";
import { useSessionStore } from "./sessionStore";

export type ChatStatus = "idle" | "sending" | "streaming" | "error";

export interface ChatState {
  messages: Message[];
  status: ChatStatus;
  error: string | null;
  agentReady: boolean;

  init: () => Promise<void>;
  send: (content: string) => Promise<void>;
  reset: () => void;
  cancel: () => Promise<void>;
}

let chunkUnsub: (() => void) | null = null;
let toolCallUnsub: (() => void) | null = null;
let toolResultUnsub: (() => void) | null = null;
let statusUnsub: (() => void) | null = null;

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
          const exists = s.messages.some((m) => m.id === data.message_id);
          const next = exists
            ? s.messages.map((m) =>
                m.id === data.message_id
                  ? {
                      ...m,
                      text: m.text + (data.delta || ""),
                      streaming: !data.done,
                    }
                  : m,
              )
            : ensureMessage(s.messages, {
                id: data.message_id,
                role: "assistant",
                text: data.delta || "",
                streaming: !data.done,
                created_at: Date.now(),
              });
          return {
            messages: next,
            status: data.done ? "idle" : "streaming",
          };
        });
      });
    }
    if (!toolCallUnsub) {
      toolCallUnsub = ipc.on<ToolCallData>(StreamEvent.ToolCall, (env) => {
        const data = env.data;
        if (!data) return;
        const id = `tc-${data.tool_call_id}`;
        set((s) => ({
          messages: ensureMessage(s.messages, {
            id,
            role: "tool",
            text: `🔧 ${data.name}(${JSON.stringify(data.args)})`,
            tool_call_id: data.tool_call_id,
            tool_name: data.name,
            tool_args: data.args,
            created_at: Date.now(),
            streaming: false,
          }),
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
          messages: ensureMessage(s.messages, {
            id,
            role: "tool",
            text,
            tool_call_id: data.tool_call_id,
            created_at: Date.now(),
            streaming: false,
          }),
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

  send: async (content: string) => {
    const text = content.trim();
    if (!text) return;
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      text,
      streaming: false,
      created_at: Date.now(),
    };
    set((s) => ({
      messages: [...s.messages, userMessage],
      status: "sending",
      error: null,
    }));
    const sessionId = useSessionStore.getState().currentSessionId;
    try {
      const result = await typedIPC.sendMessage({
        session_id: sessionId,
        content: text,
      });
      if (result.session_id && !sessionId) {
        useSessionStore.getState().setCurrent(result.session_id);
      }
      // Add a streaming assistant message; the chunk events will
      // append to it.
      set((s) => ({
        messages: ensureMessage(s.messages, {
          id: result.message_id,
          role: "assistant",
          text: "",
          streaming: true,
          created_at: Date.now(),
        }),
        status: "streaming",
      }));
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
        messages: [
          ...s.messages,
          {
            id: `err-${Date.now()}`,
            role: "system",
            text: `Error: ${message}`,
            streaming: false,
            created_at: Date.now(),
          },
        ],
      }));
      toast.error("Send failed", message);
    }
  },

  cancel: async () => {
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

  reset: () => {
    set({ messages: [], status: "idle", error: null });
  },
}));

export type { Message };
