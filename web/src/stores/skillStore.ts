/**
 * Skill store — list of installed skills + enable / disable actions.
 *
 * Wire contract (see `agent/minimax_code/ipc/handlers_skill.py`):
 *   skill.list    -> { skills: SkillInfo[] }
 *   skill.enable  -> { ok: true }
 *   skill.disable -> { ok: true }
 *
 * Note: the wire does NOT echo the new `SkillInfo` record back for
 * enable/disable, so we update the local state from our own copy
 * (the skill's `enabled` flag flips) without round-tripping the
 * server. This keeps the UI snappy; `refresh()` re-syncs on demand.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";
import type { SkillInfo } from "../types/ipc";

export type SkillEntry = SkillInfo;

export interface SkillState {
  skills: SkillEntry[];
  loading: boolean;

  refresh: () => Promise<void>;
  enable: (skillId: string) => Promise<void>;
  disable: (skillId: string) => Promise<void>;
  setEnabled: (skillId: string, enabled: boolean) => Promise<void>;
}

export const useSkillStore = create<SkillState>((set, get) => ({
  skills: [],
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listSkills();
      set({ skills: r.skills, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load skills", message);
    }
  },

  enable: async (skillId) => {
    // Optimistic update — flip locally, revert on failure.
    const prev = get().skills;
    set({
      skills: prev.map((s) => (s.id === skillId ? { ...s, enabled: true } : s)),
    });
    try {
      await typedIPC.enableSkill(skillId);
    } catch (err) {
      set({ skills: prev });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to enable skill", message);
    }
  },

  disable: async (skillId) => {
    const prev = get().skills;
    set({
      skills: prev.map((s) =>
        s.id === skillId ? { ...s, enabled: false } : s,
      ),
    });
    try {
      await typedIPC.disableSkill(skillId);
    } catch (err) {
      set({ skills: prev });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to disable skill", message);
    }
  },

  setEnabled: async (skillId, enabled) => {
    if (enabled) {
      await get().enable(skillId);
    } else {
      await get().disable(skillId);
    }
  },
}));
