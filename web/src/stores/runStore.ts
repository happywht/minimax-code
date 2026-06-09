import { create } from "zustand";
import { ipc } from "../ipc";
import {
  StreamEvent,
  type AgentRun,
  type AgentRunStep,
  type RunCompletedData,
  type RunCreatedData,
  type RunStepData,
} from "../types/ipc";

export interface RunTimelineEntry extends AgentRun {
  steps: AgentRunStep[];
}

export interface RunTimelineState {
  runs: Record<string, RunTimelineEntry>;
  order: string[];
  initialized: boolean;
  init: () => void;
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

  init: () => {
    if (get().initialized) return;

    if (!createdUnsub) {
      createdUnsub = ipc.on<RunCreatedData>(StreamEvent.RunCreated, (env) => {
        const run = env.data?.run;
        if (!run) return;
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
        set((s) => ({ runs: upsertRun(s.runs, run) }));
      });
    }

    set({ initialized: true });
  },

  reset: () => {
    set({ runs: {}, order: [], initialized: false });
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
