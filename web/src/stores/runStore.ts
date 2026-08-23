import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import {
  StreamEvent,
  type AgentRun,
  type AgentRunStep,
  type RunCompletedData,
  type RunCreatedData,
  type RunStepData,
} from "../types/ipc";
import { useSessionStore } from "./sessionStore";

export interface RunTimelineEntry extends AgentRun {
  steps: AgentRunStep[];
}

export interface RunTimelineState {
  runs: Record<string, RunTimelineEntry>;
  order: string[];
  initialized: boolean;
  loading: boolean;
  error: string | null;
  init: () => void;
  loadForSession: (sessionId: string | null) => Promise<void>;
  reset: () => void;
}

let createdUnsub: (() => void) | null = null;
let startedUnsub: (() => void) | null = null;
let completedStepUnsub: (() => void) | null = null;
let completedRunUnsub: (() => void) | null = null;

const MAX_RUNS = 20;

function upsertRun(
  runs: Record<string, RunTimelineEntry>,
  run: AgentRun,
): Record<string, RunTimelineEntry> {
  const existing = runs[run.id];
  return {
    ...runs,
    [run.id]: {
      ...existing,
      ...run,
      steps: existing?.steps ?? [],
    },
  };
}

function upsertStep(
  runs: Record<string, RunTimelineEntry>,
  runId: string,
  step: AgentRunStep,
): Record<string, RunTimelineEntry> {
  const existing = runs[runId];
  if (!existing) return runs;
  const steps = existing.steps.some((s) => s.id === step.id)
    ? existing.steps.map((s) => (s.id === step.id ? { ...s, ...step } : s))
    : [...existing.steps, step];
  steps.sort((a, b) => a.ordinal - b.ordinal);
  return {
    ...runs,
    [runId]: {
      ...existing,
      steps,
    },
  };
}

function trimOrder(order: string[]): string[] {
  return order.slice(0, MAX_RUNS);
}

export const useRunTimelineStore = create<RunTimelineState>((set, get) => ({
  runs: {},
  order: [],
  initialized: false,
  loading: false,
  error: null,

  init: () => {
    if (get().initialized) return;

    if (!createdUnsub) {
      createdUnsub = ipc.on<RunCreatedData>(StreamEvent.RunCreated, (env) => {
        const run = env.data?.run;
        if (!run) return;
        // Session guard — run.created is broadcast for every run in the
        // process (background skills, sub-agents, other tabs). The
        // timeline panel is scoped to the currently open session.
        if (run.session_id !== useSessionStore.getState().currentSessionId) return;
        set((s) => {
          const order = trimOrder([run.id, ...s.order.filter((id) => id !== run.id)]);
          const keep = new Set(order);
          const nextRuns = upsertRun(s.runs, run);
          for (const id of Object.keys(nextRuns)) {
            if (!keep.has(id)) delete nextRuns[id];
          }
          return { runs: nextRuns, order };
        });
      });
    }

    if (!startedUnsub) {
      startedUnsub = ipc.on<RunStepData>(StreamEvent.RunStepStarted, (env) => {
        const data = env.data;
        if (!data?.step) return;
        set((s) => ({ runs: upsertStep(s.runs, data.run_id, data.step) }));
      });
    }

    if (!completedStepUnsub) {
      completedStepUnsub = ipc.on<RunStepData>(StreamEvent.RunStepCompleted, (env) => {
        const data = env.data;
        if (!data?.step) return;
        set((s) => ({ runs: upsertStep(s.runs, data.run_id, data.step) }));
      });
    }

    if (!completedRunUnsub) {
      completedRunUnsub = ipc.on<RunCompletedData>(StreamEvent.RunCompleted, (env) => {
        const run = env.data?.run;
        if (!run) return;
        // Orphan/foreign guard — a completed run we never saw created
        // belongs to another session (its created event was filtered);
        // upserting it here would append a ghost timeline entry.
        if (
          !get().runs[run.id] &&
          run.session_id !== useSessionStore.getState().currentSessionId
        ) {
          return;
        }
        set((s) => ({ runs: upsertRun(s.runs, run) }));
      });
    }

    set({ initialized: true });
  },

  loadForSession: async (sessionId) => {
    if (!sessionId) return;
    get().init();
    set({ loading: true, error: null });
    try {
      const { runs } = await typedIPC.listRuns({ session_id: sessionId, limit: MAX_RUNS });
      const entries = await Promise.all(
        runs.map(async (run) => {
          try {
            return await typedIPC.getRunSteps(run.id);
          } catch {
            return { run, steps: [] };
          }
        }),
      );
      // Stale guard — the user may have switched sessions while the
      // per-run step fetches were in flight; don't splice the old
      // session's timeline into the newly opened one.
      if (useSessionStore.getState().currentSessionId !== sessionId) return;
      set(() => {
        let nextRuns: Record<string, RunTimelineEntry> = {};
        const loadedOrder: string[] = [];
        for (const entry of entries) {
          nextRuns = upsertRun(nextRuns, entry.run);
          for (const step of entry.steps) {
            nextRuns = upsertStep(nextRuns, entry.run.id, step);
          }
          loadedOrder.push(entry.run.id);
        }
        // Replace, don't merge — this store is scoped to one session;
        // leftovers from a previously opened session must not linger.
        const order = trimOrder(loadedOrder);
        return { runs: nextRuns, order, loading: false };
      });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  reset: () => {
    set({ runs: {}, order: [], initialized: false, loading: false, error: null });
  },
}));

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    createdUnsub?.();
    startedUnsub?.();
    completedStepUnsub?.();
    completedRunUnsub?.();
    createdUnsub = null;
    startedUnsub = null;
    completedStepUnsub = null;
    completedRunUnsub = null;
  });
}
