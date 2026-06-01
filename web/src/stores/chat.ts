/**
 * Chat store — the only piece of state the skeleton UI needs.
 *
 * State machine for the demo:
 *   idle → sending → streaming → idle
 *
 * Each agent reply is rendered as a single message whose `text` is
 * appended-to as `agent.message_chunk` events arrive.
 */

import { create } from "zustand";
import { ipc } from "../ipc";
import type { MessageChunkData, SendMessageResult } from "../types/ipc";

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  /** True while a stream is still in progress. */
  streaming: boolean;
  createdAt: number;
}

export interface ChatState {
  messages: Message[];
  status: "idle" | "sending" | "streaming" | "error";
  error: string | null;
  agentReady: boolean;
  init: () => Promise<void>;
  send: (content: string) => Promise<void>;
  reset: () => void;
}

let chunkUnsub: (() => void) | null = null;

export const useChat = create<ChatState>((set, get) => ({
  messages: [],
  status: "idle",
  error: null,
  agentReady: false,

  init: async () => {
    if (get().agentReady) return;
    await ipc.start();
    if (!chunkUnsub) {
      chunkUnsub = ipc.on<MessageChunkData>("agent.message_chunk", (env) => {
        const data = env.data;
        if (!data) return;
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === data.message_id
              ? {
                  ...m,
                  text: m.text + (data.delta || ""),
                  streaming: !data.done,
                }
              : m,
          ),
          status: data.done ? "idle" : "streaming",
        }));
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
      createdAt: Date.now(),
    };
    set((s) => ({
      messages: [...s.messages, userMessage],
      status: "sending",
      error: null,
    }));
    try {
      const result = await ipc.request<SendMessageResult>(
        "agent.send_message",
        { content: text, session_id: null },
      );
      // Add a streaming assistant message; the chunk events will
      // append to it.
      const assistant: Message = {
        id: result.message_id,
        role: "assistant",
        text: "",
        streaming: true,
        createdAt: Date.now(),
      };
      set((s) => ({
        messages: [...s.messages, assistant],
        status: "streaming",
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
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
            createdAt: Date.now(),
          },
        ],
      }));
    }
  },

  reset: () => {
    set({ messages: [], status: "idle", error: null });
  },
}));
