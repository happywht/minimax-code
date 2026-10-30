/**
 * Workflow management store — CRUD operations for automation workflows.
 *
 * Drives the Settings page's Workflows tab. All mutations go through
 * the TypedIPC client so the backend stays in sync.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc/client";
import type { WorkflowEntry } from "../types/ipc";
import { toast } from "../components/layout/ErrorBoundary";
import { strings } from "../ui/strings";

export interface WorkflowState {
  entries: WorkflowEntry[];
  total: number;
  loading: boolean;
  error: string | null;

  invalidateScope: () => void;
  refresh: (opts?: { trigger_type?: string; enabled_only?: boolean }) => Promise<void>;
  create: (opts: {
    name: string;
    trigger_type: string;
    description?: string;
    trigger_config?: Record<string, unknown>;
    steps?: unknown[];
    enabled?: boolean;
  }) => Promise<WorkflowEntry | null>;
  update: (
    id: string,
    fields: {
      name?: string;
      description?: string;
      trigger_config?: Record<string, unknown>;
      steps?: unknown[];
    },
  ) => Promise<void>;
  remove: (id: string) => Promise<void>;
  enable: (id: string) => Promise<void>;
  disable: (id: string) => Promise<void>;
  trigger: (id: string, context?: Record<string, unknown>) => Promise<void>;
}

let scopeEpoch = 0;

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  entries: [],
  total: 0,
  loading: false,
  error: null,

  invalidateScope: () => { scopeEpoch++; set({ entries: [], total: 0, loading: false, error: null }); },

  refresh: async (opts) => {
    const scope = scopeEpoch;
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listWorkflows(opts);
      if (scope !== scopeEpoch) return;
      set({ entries: result.entries, total: result.total, loading: false });
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowsLoadFailed, msg);
      set({ error: msg, loading: false });
    }
  },

  create: async (opts) => {
    const scope = scopeEpoch;
    try {
      const wf = await typedIPC.createWorkflow(opts);
      if (scope !== scopeEpoch) return null;
      await get().refresh();
      return scope === scopeEpoch ? wf : null;
    } catch (e) {
      if (scope !== scopeEpoch) return null;
      const msg = String(e);
      toast.error(strings.toasts.workflowCreateFailed, msg);
      set({ error: msg });
      return null;
    }
  },

  update: async (id, fields) => {
    const scope = scopeEpoch;
    try {
      await typedIPC.updateWorkflow(id, fields);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowUpdateFailed, msg);
      set({ error: msg });
    }
  },

  remove: async (id) => {
    const scope = scopeEpoch;
    try {
      await typedIPC.deleteWorkflow(id);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowDeleteFailed, msg);
      set({ error: msg });
    }
  },

  enable: async (id) => {
    const scope = scopeEpoch;
    try {
      await typedIPC.enableWorkflow(id);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowEnableFailed, msg);
      set({ error: msg });
    }
  },

  disable: async (id) => {
    const scope = scopeEpoch;
    try {
      await typedIPC.disableWorkflow(id);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowDisableFailed, msg);
      set({ error: msg });
    }
  },

  trigger: async (id, context) => {
    const scope = scopeEpoch;
    try {
      const result = await typedIPC.triggerWorkflow(id, context);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
      if (!result.ok || result.steps_failed > 0) {
        const msg = result.errors?.map((e) => `步骤 ${e.step}: ${e.message}`).join("; ")
          || `${result.steps_failed} 个工作流步骤失败`;
        toast.error(strings.toasts.workflowTriggerFailed, msg);
        set({ error: msg });
      }
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.workflowTriggerFailed, msg);
      set({ error: msg });
    }
  },
}));
