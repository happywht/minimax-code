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

export interface WorkflowState {
  entries: WorkflowEntry[];
  total: number;
  loading: boolean;
  error: string | null;

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

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  entries: [],
  total: 0,
  loading: false,
  error: null,

  refresh: async (opts) => {
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listWorkflows(opts);
      set({ entries: result.entries, total: result.total, loading: false });
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to load workflows", msg);
      set({ error: msg, loading: false });
    }
  },

  create: async (opts) => {
    try {
      const wf = await typedIPC.createWorkflow(opts);
      await get().refresh();
      return wf;
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to create workflow", msg);
      set({ error: msg });
      return null;
    }
  },

  update: async (id, fields) => {
    try {
      await typedIPC.updateWorkflow(id, fields);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to update workflow", msg);
      set({ error: msg });
    }
  },

  remove: async (id) => {
    try {
      await typedIPC.deleteWorkflow(id);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to delete workflow", msg);
      set({ error: msg });
    }
  },

  enable: async (id) => {
    try {
      await typedIPC.enableWorkflow(id);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to enable workflow", msg);
      set({ error: msg });
    }
  },

  disable: async (id) => {
    try {
      await typedIPC.disableWorkflow(id);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to disable workflow", msg);
      set({ error: msg });
    }
  },

  trigger: async (id, context) => {
    try {
      await typedIPC.triggerWorkflow(id, context);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to trigger workflow", msg);
      set({ error: msg });
    }
  },
}));
