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
  adopt: (session: TerminalSession) => void;
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
  const current = state.sessions[session.id];
  return {
    sessions: { ...state.sessions, [session.id]: current && current.updated_at > session.updated_at ? current : session },
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

  adopt: (session) =>
    set((state) => ({
      ...upsertSession(state, session),
      activeId: session.id,
      chunks: { ...state.chunks, [session.id]: state.chunks[session.id] ?? [] },
      lastSeq: { ...state.lastSeq, [session.id]: state.lastSeq[session.id] ?? 0 },
    })),

  list: async () => {
    const before = get().sessions;
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listTerminals();
      set((state) => {
        const sessions = Object.fromEntries(result.sessions.map((session) => {
          const current = state.sessions[session.id];
          return [session.id, current && current.updated_at > session.updated_at ? current : session];
        }));
        // A read or start completed while the list was in flight. Keep that
        // newer observation until the next list can confirm server state.
        const concurrent = state.order.filter((id) => !sessions[id] && state.sessions[id] !== before[id]);
        for (const id of concurrent) sessions[id] = state.sessions[id];
        const order = [...concurrent, ...result.sessions.map((session) => session.id)];
        return {
          sessions,
          order,
          activeId: state.activeId && sessions[state.activeId] ? state.activeId : order[0] ?? null,
          loading: false,
        };
      });
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
      set((state) => {
        const current = state.chunks[sessionId] ?? [];
        const chunks = new Map(current.map((chunk) => [chunk.seq, chunk]));
        for (const chunk of result.chunks) chunks.set(chunk.seq, chunk);
        const ordered = [...chunks.values()].sort((a, b) => a.seq - b.seq);
        const maxSeq = ordered.reduce((max, chunk) => Math.max(max, chunk.seq), state.lastSeq[sessionId] ?? 0);
        return {
          ...upsertSession(state, result.session),
          chunks: { ...state.chunks, [sessionId]: ordered },
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
