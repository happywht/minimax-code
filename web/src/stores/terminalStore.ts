import { create } from "zustand";
import { typedIPC } from "../ipc";
import type { TerminalChunk, TerminalSession } from "../types/ipc";

export interface TerminalState {
  sessions: Record<string, TerminalSession>;
  order: string[];
  activeId: string | null;
  chunks: Record<string, TerminalChunk[]>;
  lastSeq: Record<string, number>;
  loading: boolean;
  error: string | null;
  setActive: (sessionId: string | null) => void;
  list: () => Promise<void>;
  start: (opts: {
    command: string;
    cwd?: string;
    timeout_s?: number;
    session_id?: string | null;
  }) => Promise<TerminalSession | null>;
  read: (sessionId: string) => Promise<TerminalSession | null>;
  stop: (sessionId: string) => Promise<TerminalSession | null>;
  reset: () => void;
}

function upsertSession(
  state: Pick<TerminalState, "sessions" | "order">,
  session: TerminalSession,
): Pick<TerminalState, "sessions" | "order"> {
  const exists = session.id in state.sessions;
  return {
    sessions: { ...state.sessions, [session.id]: session },
    order: exists ? state.order : [session.id, ...state.order],
  };
}

export const useTerminalStore = create<TerminalState>((set, get) => ({
  sessions: {},
  order: [],
  activeId: null,
  chunks: {},
  lastSeq: {},
  loading: false,
  error: null,

  setActive: (activeId) => set({ activeId }),

  list: async () => {
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listTerminals();
      const sessions = Object.fromEntries(result.sessions.map((session) => [session.id, session]));
      set((state) => ({
        sessions,
        order: result.sessions.map((session) => session.id),
        activeId: state.activeId && sessions[state.activeId] ? state.activeId : result.sessions[0]?.id ?? null,
        loading: false,
      }));
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : String(err) });
    }
  },

  start: async (opts) => {
    const command = opts.command.trim();
    if (!command) return null;
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.startTerminal({ ...opts, command });
      const session = result.session;
      set((state) => ({
        ...upsertSession(state, session),
        activeId: session.id,
        chunks: { ...state.chunks, [session.id]: [] },
        lastSeq: { ...state.lastSeq, [session.id]: 0 },
        loading: false,
      }));
      await get().read(session.id);
      return session;
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  read: async (sessionId) => {
    const afterSeq = get().lastSeq[sessionId] ?? 0;
    try {
      const result = await typedIPC.readTerminal({ session_id: sessionId, after_seq: afterSeq });
      const maxSeq = result.chunks.reduce((max, chunk) => Math.max(max, chunk.seq), afterSeq);
      set((state) => {
        const current = state.chunks[sessionId] ?? [];
        return {
          ...upsertSession(state, result.session),
          chunks: { ...state.chunks, [sessionId]: [...current, ...result.chunks] },
          lastSeq: { ...state.lastSeq, [sessionId]: maxSeq },
          error: null,
        };
      });
      return result.session;
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  stop: async (sessionId) => {
    try {
      const result = await typedIPC.stopTerminal(sessionId);
      set((state) => ({ ...upsertSession(state, result.session), error: null }));
      await get().read(sessionId);
      return result.session;
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  reset: () =>
    set({
      sessions: {},
      order: [],
      activeId: null,
      chunks: {},
      lastSeq: {},
      loading: false,
      error: null,
    }),
}));
