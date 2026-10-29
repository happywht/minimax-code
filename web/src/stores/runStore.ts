import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import {
  StreamEvent,
  type AgentRun,
  type AgentRunStep,
  type RunStepsResult,
} from "../types/ipc";
import { useSessionStore } from "./sessionStore";
import { useChat } from "./chat";

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

const MAX_RUNS = 20;
let unsubs: (() => void)[] = [];
let session: string | null = null;
let generation = 0;
let snapshots = new Map<string, RunStepsResult>();
let pending = false;
let forceSnapshot = false;
let worker: Promise<void> | null = null;
let conversationStamp = "";

function validateSnapshot(snapshot: RunStepsResult, runId: string, sessionId: string): RunStepsResult {
  if (snapshot.run.id !== runId || snapshot.run.session_id !== sessionId ||
      !Number.isSafeInteger(snapshot.cursor) || snapshot.cursor < 0 ||
      snapshot.steps.some((step) => step.run_id !== runId || step.session_id !== sessionId) ||
      (snapshot.pending_ask_user != null && (snapshot.pending_ask_user.run_id !== runId || snapshot.pending_ask_user.session_id !== sessionId))) {
    throw new Error("Invalid run snapshot; refresh the timeline.");
  }
  return snapshot;
}

async function catchUp(runId: string, sessionId: string, refresh: boolean): Promise<RunStepsResult> {
  const full = async () => validateSnapshot(await typedIPC.getRunSteps(runId), runId, sessionId);
  let entry = snapshots.get(runId);
  if (refresh || !entry) return full();
  // Bound a busy run's catch-up. A fresh snapshot replaces excessively long replay.
  for (let page = 0; page < 10; page++) {
    let result;
    try {
      result = await typedIPC.getRunEvents(runId, { after_seq: entry.cursor, limit: 100 });
    } catch (err) {
      // Import/restore can replace the journal and invalidate a browser's cursor.
      if (typeof err === "object" && err !== null && "code" in err && err.code === -32602) return full();
      throw err;
    }
    let run = entry.run;
    const steps = new Map(entry.steps.map((step) => [step.id, step]));
    let cursor = entry.cursor;
    for (const event of result.events) {
      if (event.run_id !== runId || event.seq !== cursor + 1) return full();
      if (event.data.run) run = event.data.run;
      if (event.data.step) steps.set(event.data.step.id, event.data.step);
      cursor = event.seq;
    }
    if (result.next_seq !== cursor || (result.has_more && cursor === entry.cursor)) return full();
    entry = validateSnapshot({ run, steps: [...steps.values()], cursor }, runId, sessionId);
    if (!result.has_more) return entry;
  }
  return full();
}

async function drain(): Promise<void> {
  while (pending) {
    pending = false;
    const current = session;
    const epoch = generation;
    const refresh = forceSnapshot;
    const askVersion = useChat.getState().askRecoveryVersion();
    forceSnapshot = false;
    if (!current) continue;
    const isCurrent = () => epoch === generation && current === useSessionStore.getState().currentSessionId;
    try {
      const { runs } = await typedIPC.listRuns({ session_id: current, limit: MAX_RUNS });
      const entries = await Promise.all(runs.map((run) => catchUp(run.id, current, refresh)));
      if (!isCurrent()) continue;
      snapshots = new Map(entries.map((entry) => [entry.run.id, entry]));
      const next: Record<string, RunTimelineEntry> = {};
      for (const entry of entries) {
        next[entry.run.id] = {
          ...entry.run,
          steps: [...entry.steps].sort((a, b) => a.ordinal - b.ordinal || a.id.localeCompare(b.id)),
        };
      }
      useRunTimelineStore.setState({ runs: next, order: entries.map((entry) => entry.run.id), error: null });
      if (refresh && entries.length && entries.every((entry) => "pending_ask_user" in entry)) {
        const question = entries.map((entry) => entry.pending_ask_user).find((value) => value != null) ?? null;
        useChat.getState().reconcileAskUser(question, askVersion, current);
      }
      const stamp = entries.map((entry) => `${entry.run.id}:${entry.cursor}`).join("|");
      if (refresh || stamp !== conversationStamp) {
        conversationStamp = stamp;
        const active = entries.some((entry) => !["completed", "failed", "cancelled", "interrupted", "budget_paused"].includes(entry.run.status));
        void useChat.getState().reconcileRuns(entries.map((entry) => entry.run),
          () => isCurrent() && (active || conversationStamp === stamp), refresh);
      }
    } catch (err) {
      if (isCurrent()) useRunTimelineStore.setState({ error: err instanceof Error ? err.message : String(err) });
    }
  }
}

function requestSync(sessionId: string | null, refresh = false): Promise<void> {
  if (session !== sessionId) {
    session = sessionId;
    generation++;
    snapshots.clear();
    conversationStamp = "";
    useRunTimelineStore.setState({ runs: {}, order: [], error: null });
    refresh = true;
  }
  if (!sessionId) {
    pending = false;
    useRunTimelineStore.setState({ runs: {}, order: [], loading: false, error: null });
    return Promise.resolve();
  }
  pending = true;
  forceSnapshot ||= refresh;
  useRunTimelineStore.setState({ loading: true });
  if (!worker) {
    // Start on the next microtask so all notifications in this batch coalesce.
    worker = Promise.resolve().then(drain).finally(() => {
      worker = null;
      if (pending) void requestSync(session);
      else useRunTimelineStore.setState({ loading: false });
    });
  }
  return worker;
}

export const useRunTimelineStore = create<RunTimelineState>((set, get) => ({
  runs: {}, order: [], initialized: false, loading: false, error: null,
  init: () => {
    if (get().initialized) return;
    const wake = () => { void requestSync(useSessionStore.getState().currentSessionId); };
    // Notifications are wakeups; durable snapshots/events are the source of truth.
    unsubs = [
      ...[StreamEvent.RunCreated, StreamEvent.RunUpdated, StreamEvent.RunStepStarted, StreamEvent.RunStepCompleted,
        StreamEvent.RunCompleted].map((event) => ipc.on(event, wake)),
      ipc.onSideCar((event) => {
        if (event.status === "started") void requestSync(useSessionStore.getState().currentSessionId, true);
      }),
    ];
    set({ initialized: true });
  },
  loadForSession: (sessionId) => {
    get().init();
    return requestSync(sessionId, true);
  },
  reset: () => {
    generation++;
    session = null;
    snapshots.clear();
    conversationStamp = "";
    pending = false;
    forceSnapshot = false;
    unsubs.forEach((unsub) => unsub());
    unsubs = [];
    set({ runs: {}, order: [], initialized: false, loading: false, error: null });
  },
}));

if (import.meta.hot) {
  import.meta.hot.dispose(() => useRunTimelineStore.getState().reset());
}
