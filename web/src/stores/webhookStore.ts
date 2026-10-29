/**
 * Webhook management store — CRUD operations for webhook endpoints.
 *
 * Drives the Settings page's Webhooks tab. All mutations go through
 * the TypedIPC client so the backend stays in sync.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc/client";
import type { WebhookConfig } from "../types/ipc";
import { toast } from "../components/layout/ErrorBoundary";
import { strings } from "../ui/strings";

export interface WebhookState {
  entries: WebhookConfig[];
  total: number;
  loading: boolean;
  error: string | null;

  invalidateScope: () => void;
  refresh: (opts?: { source?: string }) => Promise<void>;
  create: (opts: {
    name: string;
    source?: string;
    action_type?: string;
    action_config?: Record<string, unknown>;
  }) => Promise<WebhookConfig | null>;
  update: (
    id: string,
    fields: Partial<
      Pick<
        WebhookConfig,
        "name" | "source" | "enabled" | "action_type" | "action_config"
      >
    >,
  ) => Promise<void>;
  remove: (id: string) => Promise<void>;
  regenerateSecret: (id: string) => Promise<WebhookConfig | null>;
}

let scopeEpoch = 0;

export const useWebhookStore = create<WebhookState>((set, get) => ({
  entries: [],
  total: 0,
  loading: false,
  error: null,

  invalidateScope: () => { scopeEpoch++; set({ entries: [], total: 0, loading: false, error: null }); },

  refresh: async (opts) => {
    const scope = scopeEpoch;
    set({ loading: true, error: null });
    try {
      const ipc = typedIPC;
      const result = await ipc.listWebhooks(opts);
      if (scope !== scopeEpoch) return;
      set({ entries: result.entries, total: result.total, loading: false });
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.webhooksLoadFailed, msg);
      set({ error: msg, loading: false });
    }
  },

  create: async (opts) => {
    const scope = scopeEpoch;
    try {
      const ipc = typedIPC;
      const wh = await ipc.createWebhook(opts);
      if (scope !== scopeEpoch) return null;
      await get().refresh();
      return scope === scopeEpoch ? wh : null;
    } catch (e) {
      if (scope !== scopeEpoch) return null;
      const msg = String(e);
      toast.error(strings.toasts.webhookCreateFailed, msg);
      set({ error: msg });
      return null;
    }
  },

  update: async (id, fields) => {
    const scope = scopeEpoch;
    try {
      const ipc = typedIPC;
      await ipc.updateWebhook(id, fields);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.webhookUpdateFailed, msg);
      set({ error: msg });
    }
  },

  remove: async (id) => {
    const scope = scopeEpoch;
    try {
      const ipc = typedIPC;
      await ipc.deleteWebhook(id);
      if (scope !== scopeEpoch) return ;
      await get().refresh();
    } catch (e) {
      if (scope !== scopeEpoch) return;
      const msg = String(e);
      toast.error(strings.toasts.webhookDeleteFailed, msg);
      set({ error: msg });
    }
  },

  regenerateSecret: async (id) => {
    const scope = scopeEpoch;
    try {
      const ipc = typedIPC;
      const wh = await ipc.regenerateWebhookSecret(id);
      if (scope !== scopeEpoch) return null;
      await get().refresh();
      return scope === scopeEpoch ? wh : null;
    } catch (e) {
      if (scope !== scopeEpoch) return null;
      const msg = String(e);
      toast.error(strings.toasts.webhookSecretFailed, msg);
      set({ error: msg });
      return null;
    }
  },
}));
