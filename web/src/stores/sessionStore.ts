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

const CURRENT_SESSION_STORAGE_KEY = "minimax-code:current-session";
let createSessionInFlight: Promise<string> | null = null;
let refreshSeq = 0;

function readStoredSessionId(): string | null {
  try {
    return window.localStorage.getItem(CURRENT_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeCurrentSessionId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(CURRENT_SESSION_STORAGE_KEY, id);
    else window.localStorage.removeItem(CURRENT_SESSION_STORAGE_KEY);
  } catch {
    // The in-memory selection still works when storage is unavailable.
  }
}

export interface SessionState {
  sessions: SessionMeta[];
  currentSessionId: string | null;
  loading: boolean;
  creating: boolean;
  filter: SessionFilter;

  refresh: (options?: { loadCurrent?: boolean }) => Promise<void>;
  create: (title?: string) => Promise<string>;
  createWorktree: (title?: string, baseRef?: string) => Promise<string>;
  archive: (id: string) => Promise<void>;
  unarchive: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  mergeSessions: (sessions: SessionMeta[]) => void;
  setCurrent: (id: string | null, loadMessages?: boolean) => void;
  setFilter: (filter: SessionFilter) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  currentSessionId: readStoredSessionId(),
  loading: false,
  creating: false,
  filter: "all",

  refresh: async (options) => {
    const seq = ++refreshSeq;
    set({ loading: true });
    try {
      const r = await typedIPC.listSessions();
      if (seq !== refreshSeq) {
        set({ loading: false });
        return;
      }
      const currentId = get().currentSessionId;
      const currentExists = !!currentId && r.sessions.some((session) => session.id === currentId);
      set({
        sessions: r.sessions,
        currentSessionId: currentExists ? currentId : null,
        loading: false,
      });
      if (!currentExists && currentId) {
        storeCurrentSessionId(null);
        useChat.getState().reset();
      }
      if (currentExists && options?.loadCurrent !== false) {
        void useChat.getState().loadMessages(currentId);
      }
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load sessions", message);
    }
  },

  create: async (title?: string) => {
    if (createSessionInFlight) return createSessionInFlight;

    const operation = (async () => {
      set({ creating: true });
      try {
        const reuseId = get().currentSessionId ?? undefined;
        const r = await typedIPC.createSession({
          title,
          reuse_empty_session_id: reuseId,
        });
        ++refreshSeq;
        const nextSession = r.session ?? {
          id: r.session_id,
          title: title ?? "New task",
          archived: false,
          created_at: Date.now(),
          updated_at: Date.now(),
          model_id: null,
          workspace_mode: "local" as const,
        };
        set((s) => ({
          sessions: s.sessions.some((session) => session.id === r.session_id)
            ? s.sessions.map((session) =>
                session.id === r.session_id ? { ...session, ...nextSession } : session,
              )
            : [nextSession, ...s.sessions],
          currentSessionId: r.session_id,
        }));
        storeCurrentSessionId(r.session_id);
        useChat.getState().reset();
        return r.session_id;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        toast.error("Failed to create session", message);
        throw err;
      } finally {
        set({ creating: false });
        createSessionInFlight = null;
      }
    })();

    createSessionInFlight = operation;
    return operation;
  },

  createWorktree: async (title?: string, baseRef?: string) => {
    try {
      const r = await typedIPC.createWorktreeSession({ title, base_ref: baseRef });
      ++refreshSeq;
      set((s) => ({
        sessions: [
          r.session ?? {
            id: r.session_id,
            title: title ?? "Worktree task",
            archived: false,
            created_at: Date.now(),
            updated_at: Date.now(),
            model_id: null,
            workspace_mode: "worktree",
            workspace_path: r.worktree_path ?? null,
            base_branch: baseRef ?? "HEAD",
          },
          ...s.sessions,
        ],
        currentSessionId: r.session_id,
      }));
      storeCurrentSessionId(r.session_id);
      useChat.getState().reset();
      return r.session_id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to create worktree task", message);
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
      const removingCurrent = get().currentSessionId === id;
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== id),
        currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
      }));
      if (removingCurrent) {
        storeCurrentSessionId(null);
        useChat.getState().reset();
      }
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

  mergeSessions: (nextSessions) => {
    if (nextSessions.length === 0) return;
    set((s) => {
      const byId = new Map(s.sessions.map((session) => [session.id, session]));
      for (const session of nextSessions) {
        byId.set(session.id, { ...byId.get(session.id), ...session });
      }
      return {
        sessions: Array.from(byId.values()).sort((a, b) => b.updated_at - a.updated_at),
      };
    });
  },

  setCurrent: (id: string | null, loadMessages = true) => {
    ++refreshSeq;
    set({ currentSessionId: id });
    storeCurrentSessionId(id);
    useChat.getState().reset();
    // When switching sessions, load the persisted messages for
    // the new session so the chat panel shows the history.
    if (id && loadMessages) {
      void useChat.getState().loadMessages(id);
    }
  },
  setFilter: (filter: SessionFilter) => set({ filter }),

  // expose for tests
  __getState: get,
}));
