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
import { ipc, typedIPC } from "../ipc";
import {
  StreamEvent,
  type AgentRun,
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
  /**
   * Backfill the table from the persisted ``agent_runs`` rows
   * (mode=subagent) — the panel's event-only state died on every
   * agent restart. Live events win: rows already present are never
   * overwritten by the backfill.
   */
  hydrate: () => Promise<void>;
  register: (run: SubAgentRun) => void;
  /** Update an existing run from a push event payload. */
  applyProgress: (progress: SubAgentProgress) => void;
  /** Cancel a running sub-agent by run_id. */
  cancelRun: (runId: string) => Promise<void>;
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
    "cancelled",
  ];
  return allowed.includes(value as SubAgentStatus)
    ? (value as SubAgentStatus)
    : "started";
}

/** ISO string → epoch ms; 0 when missing/unparseable (keeps sort stable). */
function toEpochMs(value: string | null | undefined): number {
  if (!value) return 0;
  const t = Date.parse(value);
  return Number.isNaN(t) ? 0 : t;
}

/**
 * Map one persisted ``agent_runs`` row (mode=subagent) to the panel's
 * SubAgentRun shape. Sub-agent rows hang off their own sub-session, so
 * the parent link comes from ``metadata.parent_session_id``.
 *
 * Non-terminal DB statuses map to "started": after an agent restart a
 * lingering "running" row is almost always an orphan, and a grey idle
 * pill is more honest than a spinner that never advances.
 */
function runRowToSubAgent(row: AgentRun): SubAgentRun {
  const meta = (row.metadata ?? {}) as Record<string, unknown>;
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const status: SubAgentStatus =
    row.status === "completed" || row.status === "failed" || row.status === "cancelled"
      ? row.status
      : "started";
  const title = row.title.replace(/^\[subagent\]\s*/, "");
  const startedAt = toEpochMs(row.started_at ?? row.created_at);
  const updatedAt = toEpochMs(row.completed_at ?? row.started_at ?? row.created_at);
  const parentSessionId = str(meta.parent_session_id);
  return {
    run_id: row.id,
    agent_id: str(meta.agent_id),
    agent_name: str(meta.agent_name) || title,
    prompt: str(meta.prompt),
    parent_session_id: parentSessionId || undefined,
    status,
    progress: status === "completed" ? 1 : 0,
    summary: title,
    text: str(meta.result_text) || undefined,
    error: row.error ?? undefined,
    started_at: startedAt,
    updated_at: updatedAt,
    finished_at: status !== "started" ? updatedAt : undefined,
  };
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
    // Backfill rows persisted before this page loaded (agent restart
    // wiped the in-memory event state). Fail-open: a cold backend just
    // leaves the live stream as the only source, as before.
    await get().hydrate();
  },

  hydrate: async () => {
    try {
      const result = await typedIPC.listRuns({ mode: "subagent", limit: MAX_SUBAGENT_RUNS });
      set((s) => {
        const runs = { ...s.runs };
        for (const row of result.runs) {
          // Live events win — only fill rows this page never saw.
          if (runs[row.id] !== undefined) continue;
          runs[row.id] = runRowToSubAgent(row);
        }
        return { runs: evictOldest(runs, MAX_SUBAGENT_RUNS, (r) => r.updated_at) };
      });
    } catch {
      // Fail-open — the live event stream keeps working without the backfill.
    }
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
      const finished = status === "completed" || status === "failed" || status === "cancelled";
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
        if (v.status !== "completed" && v.status !== "failed" && v.status !== "cancelled") {
          next[k] = v;
        }
      }
      return { runs: next };
    }),

  cancelRun: async (runId: string) => {
    // Optimistic update — mark as cancelled immediately.
    set((s) => {
      const run = s.runs[runId];
      if (!run) return s;
      return {
        runs: {
          ...s.runs,
          [runId]: { ...run, status: "cancelled" as SubAgentStatus, updated_at: Date.now() },
        },
      };
    });
    try {
      await typedIPC.cancelSubagent(runId);
    } catch (err) {
      // The backend may still be running — revert optimistic status
      // to "failed" so the user knows the cancel didn't go through.
      const msg = err instanceof Error ? err.message : String(err);
      set((s) => {
        const run = s.runs[runId];
        if (!run) return s;
        return {
          runs: {
            ...s.runs,
            [runId]: { ...run, status: "failed" as SubAgentStatus, error: msg, updated_at: Date.now() },
          },
          error: msg,
        };
      });
    }
  },

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
