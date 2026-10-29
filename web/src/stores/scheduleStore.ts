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
import type { ScheduledJob, ScheduleDefinition } from "../types/ipc";
import { strings } from "../ui/strings";

export type ScheduledJobEntry = ScheduledJob;

export interface ScheduleState {
  jobs: ScheduledJobEntry[];
  loading: boolean;

  invalidateScope: (clearPending?: boolean) => void;
  refresh: () => Promise<void>;
  create: (opts: ScheduleDefinition) => Promise<ScheduledJobEntry | null>;
  remove: (jobId: string) => Promise<void>;
  /**
   * Compare-and-swap the definition; running occurrences retain their snapshot.
   */
  update: (
    jobId: string,
    opts: ScheduleDefinition,
  ) => Promise<ScheduledJobEntry | null>;
  enable: (jobId: string, acknowledgeOccurrenceId?: string) => Promise<void>;
  disable: (jobId: string) => Promise<void>;
  setEnabled: (jobId: string, enabled: boolean) => Promise<void>;
  runNow: (jobId: string) => Promise<void>;
}

const manualKeys = new Map<string, {key:string; expectedRevision:number}>();
let refreshEpoch = 0;
let scopeEpoch = 0;

export const useScheduleStore = create<ScheduleState>((set, get) => ({
  jobs: [],
  loading: false,

  invalidateScope: (clearPending = false) => { scopeEpoch++; refreshEpoch++; if (clearPending) manualKeys.clear(); set({ jobs: [], loading: false }); },

  refresh: async () => {
    const ticket = ++refreshEpoch;
    set({ loading: true });
    try {
      const r = await typedIPC.listJobs();
      if (ticket !== refreshEpoch) return;
      set({ jobs: r.jobs, loading: false });
    } catch (err) {
      if (ticket !== refreshEpoch) return;
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobsLoadFailed, message);
    }
  },

  create: async (opts) => {
    const scope = scopeEpoch;
    try {
      const r = await typedIPC.createJob(opts);
      if (scope !== scopeEpoch) return null;
      refreshEpoch++;
      set((s) => ({ jobs: [...s.jobs, r.job], loading: false }));
      return r.job;
    } catch (err) {
      if (scope !== scopeEpoch) return null;
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobCreateFailed, message);
      return null;
    }
  },

  remove: async (jobId) => {
    const scope = scopeEpoch;
    refreshEpoch++;
    const prev = get().jobs;
    // Optimistic remove — restore on failure.
    set({ jobs: prev.filter((j) => j.id !== jobId), loading: false });
    try {
      await typedIPC.deleteJob(jobId);
      if (scope !== scopeEpoch) return;
    } catch (err) {
      if (scope !== scopeEpoch) return;
      set({ jobs: prev });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobDeleteFailed, message);
    }
  },

  update: async (jobId, opts) => {
    const scope = scopeEpoch;
    const existing = get().jobs.find((job) => job.id === jobId);
    try {
      const result = await typedIPC.updateJob(jobId, existing?.revision ?? 1, opts);
      if (scope !== scopeEpoch) return null;
      refreshEpoch++;
      set((state) => ({jobs:state.jobs.map((job) => job.id === jobId ? result.job : job), loading: false}));
      return result.job;
    } catch (err) {
      if (scope !== scopeEpoch) return null;
      toast.error(strings.toasts.jobCreateFailed, err instanceof Error ? err.message : String(err));
      return null;
    }
  },

  enable: async (jobId, acknowledgeOccurrenceId) => {
    const scope = scopeEpoch;
    try {
      const r = await typedIPC.enableJob(jobId, acknowledgeOccurrenceId);
      if (scope !== scopeEpoch) return;
      refreshEpoch++;
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === jobId ? r.job : j)), loading: false,
      }));
    } catch (err) {
      if (scope !== scopeEpoch) return;
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobEnableFailed, message);
    }
  },

  disable: async (jobId) => {
    const scope = scopeEpoch;
    try {
      const r = await typedIPC.disableJob(jobId);
      if (scope !== scopeEpoch) return;
      refreshEpoch++;
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === jobId ? r.job : j)), loading: false,
      }));
    } catch (err) {
      if (scope !== scopeEpoch) return;
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
    const scope = scopeEpoch;
    try {
      const job = get().jobs.find((entry) => entry.id === jobId);
      const intent = manualKeys.get(jobId) ?? {key:crypto.randomUUID(),expectedRevision:job?.revision ?? 1};
      manualKeys.set(jobId,intent);
      await typedIPC.runNowJob(jobId,intent.key,intent.expectedRevision);
      if (scope !== scopeEpoch) return;
      manualKeys.delete(jobId);
      await get().refresh();
      toast.info(strings.toasts.jobTriggered, strings.toasts.jobTriggeredDetail(jobId));
    } catch (err) {
      if (scope !== scopeEpoch) return;
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.jobTriggerFailed, message);
    }
  },
}));
