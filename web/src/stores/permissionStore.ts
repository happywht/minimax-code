/**
 * Permission store — "always allow" toggle + per-tool rules.
 *
 * The `alwaysAllow` flag is a UI shortcut: when true, all permission
 * requests default to "allow" without showing a confirmation modal.
 * The Python sidecar still receives the per-rule list and applies it
 * server-side; this store is the canonical place to read/write it.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import type { PermissionRule } from "../types/ipc";

export type PermissionRuleEntry = PermissionRule;

export interface PermissionState {
  alwaysAllow: boolean;
  rules: PermissionRuleEntry[];
  loading: boolean;

  refresh: () => Promise<void>;
  setAlwaysAllow: (v: boolean) => void;
  upsertRule: (rule: Omit<PermissionRule, "id" | "created_at"> & { id?: string }) => Promise<void>;
  removeRule: (id: string) => Promise<void>;
}

export const usePermissionStore = create<PermissionState>((set, get) => ({
  alwaysAllow: false,
  rules: [],
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listRules();
      set({ rules: r.rules, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load permission rules", message);
    }
  },

  setAlwaysAllow: (v: boolean) => set({ alwaysAllow: v }),

  upsertRule: async (rule) => {
    try {
      const r = await typedIPC.setRule(rule);
      const existing = get().rules;
      const idx = existing.findIndex((x) => x.id === r.rule.id);
      const next =
        idx === -1
          ? [...existing, r.rule]
          : existing.map((x) => (x.id === r.rule.id ? r.rule : x));
      set({ rules: next });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to save rule", message);
    }
  },

  removeRule: async (id: string) => {
    try {
      await typedIPC.deleteRule(id);
      set((s) => ({ rules: s.rules.filter((x) => x.id !== id) }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to delete rule", message);
    }
  },
}));
