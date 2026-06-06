/**
 * Session store — owns the list of known sessions, the current
 * session id, and the section filters for the sidebar. The chat store
 * reads `currentSessionId` when sending messages.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import type { Session } from "../types/ipc";
import { useChat } from "./chat";

export type SessionFilter =
  | "all"
  | "scheduled"
  | "history"
  | "agents"
  | "archived"
  | "skills";

export type SessionMeta = Session;

export interface SessionState {
  sessions: SessionMeta[];
  currentSessionId: string | null;
  loading: boolean;
  filter: SessionFilter;

  refresh: () => Promise<void>;
  create: (title?: string) => Promise<string>;
  archive: (id: string) => Promise<void>;
  unarchive: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  setCurrent: (id: string | null) => void;
  setFilter: (filter: SessionFilter) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  loading: false,
  filter: "all",

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listSessions();
      set({ sessions: r.sessions, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load sessions", message);
    }
  },

  create: async (title?: string) => {
    try {
      const r = await typedIPC.createSession({ title });
      // Optimistically add to the list.
      set((s) => ({
        sessions: [
          {
            id: r.session_id,
            title: title ?? "New task",
            archived: false,
            created_at: Date.now(),
            updated_at: Date.now(),
            model_id: null,
          },
          ...s.sessions,
        ],
        currentSessionId: r.session_id,
      }));
      return r.session_id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to create session", message);
      throw err;
    }
  },

  archive: async (id: string) => {
    try {
      await typedIPC.archiveSession(id);
      set((s) => ({
        sessions: s.sessions.map((x) => (x.id === id ? { ...x, archived: true } : x)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Archive failed", message);
    }
  },

  unarchive: async (id: string) => {
    try {
      await typedIPC.unarchiveSession(id);
      set((s) => ({
        sessions: s.sessions.map((x) => (x.id === id ? { ...x, archived: false } : x)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Unarchive failed", message);
    }
  },

  remove: async (id: string) => {
    try {
      await typedIPC.deleteSession(id);
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== id),
        currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Delete failed", message);
    }
  },

  rename: async (id: string, title: string) => {
    try {
      const r = await typedIPC.updateSession(id, { title });
      if (r.session) {
        set((s) => ({
          sessions: s.sessions.map((x) =>
            x.id === id ? { ...x, title: r.session.title, updated_at: r.session.updated_at } : x,
          ),
        }));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Rename failed", message);
    }
  },

  setCurrent: (id: string | null) => {
    set({ currentSessionId: id });
    // When switching sessions, load the persisted messages for
    // the new session so the chat panel shows the history.
    if (id) {
      void useChat.getState().loadMessages(id);
    } else {
      useChat.getState().reset();
    }
  },
  setFilter: (filter: SessionFilter) => set({ filter }),

  // expose for tests
  __getState: get,
}));
