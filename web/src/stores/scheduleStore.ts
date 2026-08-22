/**
 * Schedule store — list of cron jobs + create / delete / enable / disable.
 *
 * The wire contract lives in `handlers_scheduled.py`:
 *   schedule.list     -> { jobs: [...] }
 *   schedule.create   -> { job: {...} }
 *   schedule.delete   -> { ok: true, job_id: ... }
 *   schedule.enable   -> { job: {...} }   # enabled = true
 *   schedule.disable  -> { job: {...} }   # enabled = false
 *
 * The JS API on the store is friendlier: `cron` and `prompt` are
 * used by the UI, and `create()` translates to the wire's
 * `cron_expr` / `payload` shape via the typedIPC binding.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { ScheduledJob } from "../types/ipc";
import { strings } from "../ui/strings";

export type ScheduledJobEntry = ScheduledJob;

export interface ScheduleState {
  jobs: ScheduledJobEntry[];
  loading: boolean;

  refresh: () => Promise<void>;
  create: (opts: { name: string; cron: string; prompt: string }) => Promise<ScheduledJobEntry | null>;
  remove: (jobId: string) => Promise<void>;
  /**
   * Replace a job's definition. The wire contract has no schedule.update,
   * so this creates the new job first and only deletes the old one on
   * success — a failed create leaves the original untouched.
   */
  update: (
    jobId: string,
    opts: { name: string; cron: string; prompt: string },
  ) => Promise<ScheduledJobEntry | null>;
  enable: (jobId: string) => Promise<void>;
  disable: (jobId: string) => Promise<void>;
  setEnabled: (jobId: string, enabled: boolean) => Promise<void>;
  runNow: (jobId: string) => Promise<void>;
}

export const useScheduleStore = create<ScheduleState>((set, get) => ({
  jobs: [],
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listJobs();
      set({ jobs: r.jobs, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobsLoadFailed, message);
    }
  },

  create: async (opts) => {
    try {
      const r = await typedIPC.createJob(opts);
      set((s) => ({ jobs: [...s.jobs, r.job] }));
      return r.job;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobCreateFailed, message);
      return null;
    }
  },

  remove: async (jobId) => {
    const prev = get().jobs;
    // Optimistic remove — restore on failure.
    set({ jobs: prev.filter((j) => j.id !== jobId) });
    try {
      await typedIPC.deleteJob(jobId);
    } catch (err) {
      set({ jobs: prev });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobDeleteFailed, message);
    }
  },

  update: async (jobId, opts) => {
    // create→delete composition: keep the old job unless the new one exists.
    const created = await get().create(opts);
    if (!created) return null;
    const prevJobs = get().jobs;
    set({ jobs: prevJobs.filter((j) => j.id !== jobId) });
    try {
      await typedIPC.deleteJob(jobId);
    } catch (err) {
      // New job exists but the stale one lingers — surface the error and
      // refresh so the list reflects reality on both ends.
      await get().refresh();
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobDeleteFailed, message);
    }
    return created;
  },

  enable: async (jobId) => {
    try {
      const r = await typedIPC.enableJob(jobId);
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === jobId ? r.job : j)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobEnableFailed, message);
    }
  },

  disable: async (jobId) => {
    try {
      const r = await typedIPC.disableJob(jobId);
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === jobId ? r.job : j)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobDisableFailed, message);
    }
  },

  setEnabled: async (jobId, enabled) => {
    if (enabled) {
      await get().enable(jobId);
    } else {
      await get().disable(jobId);
    }
  },

  runNow: async (jobId) => {
    try {
      await typedIPC.runNowJob(jobId);
      toast.info(strings.toasts.jobTriggered, strings.toasts.jobTriggeredDetail(jobId));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobTriggerFailed, message);
    }
  },
}));
