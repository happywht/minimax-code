/**
 * Agent store — CRUD management for sub-agents.
 *
 * Backed by the ``agent.*`` IPC namespace (``agent.list``,
 * ``agent.create``, ``agent.update``, ``agent.delete``).
 * The store keeps an in-memory list of agents and provides
 * convenience methods that wrap the typed IPC calls.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { AgentInfo } from "../types/ipc";

export interface AgentState {
  agents: AgentInfo[];
  loading: boolean;

  refresh: () => Promise<void>;
  create: (opts: { name: string; system_prompt: string; tool_allowlist?: string[]; model?: string }) => Promise<AgentInfo | null>;
  update: (opts: { name: string; system_prompt?: string; tool_allowlist?: string[]; model?: string; enabled?: boolean }) => Promise<void>;
  remove: (name: string) => Promise<void>;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  agents: [],
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const r = await typedIPC.listAgents();
      set({ agents: r.agents, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load agents", message);
    }
  },

  create: async (opts) => {
    try {
      const r = await typedIPC.createAgent(opts);
      set((s) => ({ agents: [...s.agents, r.agent] }));
      return r.agent;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to create agent", message);
      return null;
    }
  },

  update: async (opts) => {
    try {
      const r = await typedIPC.updateAgent(opts);
      set((s) => ({
        agents: s.agents.map((a) => (a.name === opts.name ? r.agent : a)),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to update agent", message);
    }
  },

  remove: async (name) => {
    const prev = get().agents;
    set({ agents: prev.filter((a) => a.name !== name) });
    try {
      await typedIPC.deleteAgent(name);
    } catch (err) {
      set({ agents: prev });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to delete agent", message);
    }
  },
}));
