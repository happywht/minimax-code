/**
 * Agent team management store — CRUD operations for team templates.
 *
 * Drives the Settings page's Teams tab (v0.8.0 Enterprise Multi-Agent).
 * All mutations go through the TypedIPC client so the backend stays in sync.
 */

import { create } from "zustand";
import { typedIPC } from "@/ipc/client";
import type { AgentTeam, OrchestrationMode } from "../types/ipc";

export interface TeamState {
  teams: AgentTeam[];
  loading: boolean;
  error: string | null;

  refresh: () => Promise<void>;
  create: (opts: {
    name: string;
    description?: string;
    icon?: string;
    color?: string;
    agents?: string[];
    orchestration_mode?: OrchestrationMode;
  }) => Promise<AgentTeam | null>;
  update: (
    name: string,
    fields: {
      description?: string;
      icon?: string;
      color?: string;
      agents?: string[];
      orchestration_mode?: OrchestrationMode;
    },
  ) => Promise<void>;
  remove: (name: string) => Promise<void>;
  enable: (name: string) => Promise<void>;
  disable: (name: string) => Promise<void>;
}

export const useTeamStore = create<TeamState>((set, get) => ({
  teams: [],
  loading: false,
  error: null,

  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const result = await typedIPC.listTeams();
      set({ teams: result.teams, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  create: async (opts) => {
    try {
      const team = await typedIPC.createTeam(opts);
      await get().refresh();
      return team.team;
    } catch (e) {
      set({ error: String(e) });
      return null;
    }
  },

  update: async (name, fields) => {
    try {
      await typedIPC.updateTeam(name, fields);
      await get().refresh();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  remove: async (name) => {
    try {
      await typedIPC.deleteTeam(name);
      await get().refresh();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  enable: async (name) => {
    try {
      await typedIPC.enableTeam(name);
      await get().refresh();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  disable: async (name) => {
    try {
      await typedIPC.disableTeam(name);
      await get().refresh();
    } catch (e) {
      set({ error: String(e) });
    }
  },
}));
