/**
 * Webhook management store — CRUD operations for webhook endpoints.
 *
 * Drives the Settings page's Webhooks tab. All mutations go through
 * the TypedIPC client so the backend stays in sync.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc/client";
import type { WebhookConfig } from "../types/ipc";
import { toast } from "../components/ErrorBoundary";

export interface WebhookState {
  entries: WebhookConfig[];
  total: number;
  loading: boolean;
  error: string | null;

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

export const useWebhookStore = create<WebhookState>((set, get) => ({
  entries: [],
  total: 0,
  loading: false,
  error: null,

  refresh: async (opts) => {
    set({ loading: true, error: null });
    try {
      const ipc = typedIPC;
      const result = await ipc.listWebhooks(opts);
      set({ entries: result.entries, total: result.total, loading: false });
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to load webhooks", msg);
      set({ error: msg, loading: false });
    }
  },

  create: async (opts) => {
    try {
      const ipc = typedIPC;
      const wh = await ipc.createWebhook(opts);
      await get().refresh();
      return wh;
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to create webhook", msg);
      set({ error: msg });
      return null;
    }
  },

  update: async (id, fields) => {
    try {
      const ipc = typedIPC;
      await ipc.updateWebhook(id, fields);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to update webhook", msg);
      set({ error: msg });
    }
  },

  remove: async (id) => {
    try {
      const ipc = typedIPC;
      await ipc.deleteWebhook(id);
      await get().refresh();
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to delete webhook", msg);
      set({ error: msg });
    }
  },

  regenerateSecret: async (id) => {
    try {
      const ipc = typedIPC;
      const wh = await ipc.regenerateWebhookSecret(id);
      await get().refresh();
      return wh;
    } catch (e) {
      const msg = String(e);
      toast.error("Failed to regenerate webhook secret", msg);
      set({ error: msg });
      return null;
    }
  },
}));
