/**
 * Sub-agent store — owns the in-memory table of in-flight + completed
 * sub-agent runs, keyed by run_id. Subscribes to the
 * ``agent.subagent_progress`` push event and updates the matching row
 * in place.
 *
 * The store is the single source of truth for the SubAgentPanel
 * (right-rail live list) and the SubAgentResultCard (in the chat
 * stream). Both consume the same rows; the result card just
 * filters for ``status === "completed" | "failed"``.
 */
import { create } from "zustand";
import { ipc } from "../ipc";
import {
  StreamEvent,
  type SubAgentProgress,
  type SubAgentRun,
  type SubAgentStatus,
} from "../types/ipc";
import { evictOldest, MAX_SUBAGENT_RUNS } from "../lib/eviction";

export interface SubAgentState {
  /** Keyed by run_id. */
  runs: Record<string, SubAgentRun>;
  /** Has the global IPC subscription been wired up? */
  subscribed: boolean;
  /** Last error from the IPC layer (for the UI banner). */
  error: string | null;

  init: () => Promise<void>;
  /** Register a new run (called by the spawn UI flow before the first event). */
  register: (run: SubAgentRun) => void;
  /** Update an existing run from a push event payload. */
  applyProgress: (progress: SubAgentProgress) => void;
  /** Drop every completed run (used by ``clearCompleted`` button). */
  clearCompleted: () => void;
  /** Reset state — used by tests. */
  reset: () => void;
}

let unsubProgress: (() => void) | null = null;

function coerceStatus(value: unknown): SubAgentStatus {
  const allowed: SubAgentStatus[] = [
    "started",
    "thinking",
    "tool_call",
    "tool_result",
    "completed",
    "failed",
  ];
  return allowed.includes(value as SubAgentStatus)
    ? (value as SubAgentStatus)
    : "started";
}

export const useSubAgentStore = create<SubAgentState>((set, get) => ({
  runs: {},
  subscribed: false,
  error: null,

  init: async () => {
    if (get().subscribed) return;
    await ipc.start();
    if (!unsubProgress) {
      unsubProgress = ipc.on<SubAgentProgress>(
        StreamEvent.SubAgentProgress,
        (env) => {
          const data = env.data as SubAgentProgress | undefined;
          if (!data || !data.run_id) return;
          // Normalise before applying — events from older backends
          // may not include ``received_at``.
          const payload: SubAgentProgress = {
            ...data,
            received_at:
              typeof data.received_at === "number" ? data.received_at : Date.now(),
          };
          get().applyProgress(payload);
        },
      );
    }
    set({ subscribed: true });
  },

  register: (run) =>
    set((s) => ({
      runs: evictOldest({ ...s.runs, [run.run_id]: run }, MAX_SUBAGENT_RUNS, (r) => r.updated_at),
    })),

  applyProgress: (progress) =>
    set((s) => {
      const existing = s.runs[progress.run_id];
      // If we missed the very first event (e.g. the spawn reply was
      // racy with the listener), we still need a row to update —
      // synthesise a minimal one so the UI can render the progress.
      const base: SubAgentRun =
        existing ?? {
          run_id: progress.run_id,
          agent_id: progress.agent_id,
          agent_name: progress.agent_id,
          prompt: "",
          status: "started",
          progress: 0,
          summary: "",
          started_at: progress.received_at,
          updated_at: progress.received_at,
          parent_session_id: progress.parent_session_id,
          context_message_id: progress.context_message_id,
        };
      const status = coerceStatus(progress.status);
      const finished = status === "completed" || status === "failed";
      const next: SubAgentRun = {
        ...base,
        agent_id: progress.agent_id,
        parent_session_id:
          progress.parent_session_id ?? base.parent_session_id,
        context_message_id:
          progress.context_message_id ?? base.context_message_id,
        status,
        progress:
          typeof progress.progress === "number" ? progress.progress : base.progress,
        summary: progress.summary ?? base.summary,
        text: progress.text ?? base.text,
        error: progress.error ?? base.error,
        updated_at: progress.received_at,
        finished_at: finished
          ? (base.finished_at ?? progress.received_at)
          : base.finished_at,
      };
      return { runs: evictOldest({ ...s.runs, [progress.run_id]: next }, MAX_SUBAGENT_RUNS, (r) => r.updated_at) };
    }),

  clearCompleted: () =>
    set((s) => {
      const next: Record<string, SubAgentRun> = {};
      for (const [k, v] of Object.entries(s.runs)) {
        if (v.status !== "completed" && v.status !== "failed") {
          next[k] = v;
        }
      }
      return { runs: next };
    }),

  reset: () => set({ runs: {}, subscribed: false, error: null }),
}));

/** Selector helper — runs anchored to one parent session, newest first. */
export function runsForSession(
  state: SubAgentState,
  sessionId: string | null | undefined,
): SubAgentRun[] {
  if (!sessionId) return Object.values(state.runs);
  return Object.values(state.runs).filter(
    (r) => r.parent_session_id === sessionId,
  );
}
