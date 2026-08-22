/**
 * Provider store — list of LLM providers + CRUD operations.
 * Persisted to the Python agent (which writes to SQLite + OS keyring).
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { ProviderInfo, ProviderModel } from "../types/ipc";
import { useModelStore } from "./modelStore";
import { strings } from "../ui/strings";

export interface ProviderState {
  providers: ProviderInfo[];
  loading: boolean;
  initialized: boolean;

  refresh: () => Promise<void>;
  create: (opts: {
    name: string;
    protocol: "anthropic" | "openai";
    base_url: string;
    api_key?: string;
    models?: ProviderModel[];
    enabled?: boolean;
  }) => Promise<ProviderInfo | null>;
  update: (opts: {
    provider_id: string;
    name?: string;
    protocol?: "anthropic" | "openai";
    base_url?: string;
    api_key?: string;
    models?: ProviderModel[];
    enabled?: boolean;
  }) => Promise<ProviderInfo | null>;
  remove: (providerId: string) => Promise<boolean>;
  setApiKey: (providerId: string, apiKey: string) => Promise<boolean>;
  clearApiKey: (providerId: string) => Promise<boolean>;
}

export const useProviderStore = create<ProviderState>((set, get) => ({
  providers: [],
  loading: false,
  initialized: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listProviders();
      set({ providers: r.providers, loading: false, initialized: true });
    } catch (err) {
      set({ loading: false, initialized: true });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.providersLoadFailed, message);
    }
  },

  create: async (opts) => {
    try {
      const r = await typedIPC.createProvider(opts);
      await get().refresh();
      await useModelStore.getState().refresh();
      return r.provider;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.providerCreateFailed, message);
      return null;
    }
  },

  update: async (opts) => {
    try {
      const r = await typedIPC.updateProvider(opts);
      await get().refresh();
      await useModelStore.getState().refresh();
      return r.provider;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.providerUpdateFailed, message);
      return null;
    }
  },

  remove: async (providerId) => {
    try {
      await typedIPC.deleteProvider(providerId);
      await get().refresh();
      await useModelStore.getState().refresh();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.providerDeleteFailed, message);
      return false;
    }
  },

  setApiKey: async (providerId, apiKey) => {
    try {
      const r = await typedIPC.setProviderApiKey(providerId, apiKey);
      await get().refresh();
      set((s) => ({
        providers: s.providers.map((p) =>
          p.id === providerId ? { ...p, api_key_configured: r.api_key_configured } : p,
        ),
      }));
      await useModelStore.getState().refresh();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.apiKeySetFailed, message);
      return false;
    }
  },

  clearApiKey: async (providerId) => {
    try {
      const r = await typedIPC.clearProviderApiKey(providerId);
      await get().refresh();
      set((s) => ({
        providers: s.providers.map((p) =>
          p.id === providerId ? { ...p, api_key_configured: r.api_key_configured } : p,
        ),
      }));
      await useModelStore.getState().refresh();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.apiKeyClearFailed, message);
      return false;
    }
  },
}));
