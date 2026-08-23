/**
 * Task store — long-running task progress entries.
 *
 * Updated by the ``task.progress`` IPC event from the Python agent and
 * hydrated from the persisted ``task.list`` ledger on demand. The store
 * maps backend status values (``pending``/``running``/``completed``/
 * ``failed``/``cancelled``) to the frontend ``TaskStatus`` union.
 */

import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { StreamEvent, type TaskProgressData, type TaskRow } from "../types/ipc";
import { evictOldest, MAX_TASKS } from "../lib/eviction";

export type TaskStatus = "pending" | "running" | "done" | "error" | "cancelled";

export interface TaskProgressEntry {
  task_id: string;
  status: TaskStatus;
  progress: number;
  message?: string;
  /** v1.2.0 — the run's persisted result text (LLM reply for prompt payloads). */
  result?: string;
  updated_at: number;
}

export interface TaskState {
  tasks: Record<string, TaskProgressEntry>;
  collapsed: boolean;

  startListening: () => () => void;
  upsert: (data: TaskProgressData) => void;
  remove: (taskId: string) => void;
  setCollapsed: (collapsed: boolean) => void;
  clear: () => void;
  refresh: () => Promise<void>;
  cancel: (taskId: string) => Promise<boolean>;
}

/** Map a backend task status to the frontend ``TaskStatus`` union. */
function normalizeStatus(status: string): TaskStatus {
  switch (status) {
    case "pending":
      return "pending";
    case "running":
      return "running";
    case "completed":
      return "done";
    case "failed":
      return "error";
    case "cancelled":
      return "cancelled";
    default:
      return "pending";
  }
}

/** Build a store entry from a backend ``task.list`` row. */
function entryFromRow(row: TaskRow): TaskProgressEntry {
  const status = normalizeStatus(row.status);
  const message = status === "error" && row.error ? row.error : undefined;
  const updatedAtRaw = row.completed_at || row.created_at;
  const updated_at = updatedAtRaw ? new Date(updatedAtRaw).getTime() || Date.now() : Date.now();
  return {
    task_id: row.id,
    status,
    progress: Math.max(0, Math.min(1, row.progress / 100)),
    message,
    result: row.result ?? undefined,
    updated_at,
  };
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: {},
  collapsed: false,

  startListening: () => {
    return ipc.on<TaskProgressData>(StreamEvent.TaskProgress, (env) => {
      if (env.data) {
        get().upsert(env.data);
      }
    });
  },

  upsert: (data) => {
    const prev = get().tasks[data.task_id];
    const status = normalizeStatus(data.status);
    set((s) => {
      const updated = {
        ...s.tasks,
        [data.task_id]: {
          // Spread the previous entry so fields the event payload cannot
          // carry (``result`` only lives in the persisted ledger) survive
          // progress ticks instead of being clobbered back to undefined.
          ...s.tasks[data.task_id],
          task_id: data.task_id,
          status,
          progress: Math.max(0, Math.min(1, data.progress)),
          message: data.message,
          updated_at: Date.now(),
        },
      };
      return { tasks: evictOldest(updated, MAX_TASKS, (e) => e.updated_at) };
    });
    // The ``task.progress`` event fires before the scheduler persists the
    // final row, so a terminal transition cannot carry the result text yet.
    // Re-hydrate from the ledger once the task settles — refresh() only
    // merges rows and never emits events, so this cannot loop.
    const wasLive = !prev || prev.status === "pending" || prev.status === "running";
    const terminal = status === "done" || status === "error" || status === "cancelled";
    if (wasLive && terminal) {
      void get().refresh();
    }
  },

  remove: (taskId) =>
    set((s) => {
      const { [taskId]: _, ...rest } = s.tasks;
      void _;
      return { tasks: rest };
    }),

  setCollapsed: (collapsed) => set({ collapsed }),
  clear: () => set({ tasks: {} }),

  refresh: async () => {
    const result = await typedIPC.listTasks({ limit: 100 });
    const entries = result.tasks.map(entryFromRow);
    set((s) => {
      const next: Record<string, TaskProgressEntry> = { ...s.tasks };
      for (const entry of entries) {
        // Persisted ledger wins over in-memory event state.
        next[entry.task_id] = entry;
      }
      return { tasks: evictOldest(next, MAX_TASKS, (e) => e.updated_at) };
    });
  },

  cancel: async (taskId) => {
    try {
      await typedIPC.cancelTask(taskId);
      set((s) => ({
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...s.tasks[taskId],
            status: "cancelled",
            progress: 1,
            updated_at: Date.now(),
          },
        },
      }));
      return true;
    } catch {
      return false;
    }
  },
}));
