/**
 * Task store — long-running task progress entries. Updated by the
 * `task.progress` IPC event from the Python agent and consumed by the
 * ProgressPanel.
 */

import { create } from "zustand";
import { ipc } from "../ipc";
import { StreamEvent, type TaskProgressData } from "../types/ipc";
import { evictOldest, MAX_TASKS } from "../lib/eviction";

export type TaskStatus = "running" | "done" | "error" | "cancelled";

export interface TaskProgressEntry {
  task_id: string;
  status: TaskStatus;
  progress: number;
  message?: string;
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
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: {},
  collapsed: false,

  startListening: () => {
    return ipc.on<TaskProgressData>(StreamEvent.TaskProgress, (env) => {
      if (env.data) {
        set((s) => {
          const updated = {
            ...s.tasks,
            [env.data!.task_id]: {
              task_id: env.data!.task_id,
              status: env.data!.status,
              progress: Math.max(0, Math.min(1, env.data!.progress)),
              message: env.data!.message,
              updated_at: Date.now(),
            },
          };
          return { tasks: evictOldest(updated, MAX_TASKS, (e) => e.updated_at) };
        });
      }
    });
  },

  upsert: (data) =>
    set((s) => {
      const updated = {
        ...s.tasks,
        [data.task_id]: {
          task_id: data.task_id,
          status: data.status,
          progress: Math.max(0, Math.min(1, data.progress)),
          message: data.message,
          updated_at: Date.now(),
        },
      };
      return { tasks: evictOldest(updated, MAX_TASKS, (e) => e.updated_at) };
    }),

  remove: (taskId) =>
    set((s) => {
      const { [taskId]: _, ...rest } = s.tasks;
      void _;
      return { tasks: rest };
    }),

  setCollapsed: (collapsed) => set({ collapsed }),
  clear: () => set({ tasks: {} }),
}));
