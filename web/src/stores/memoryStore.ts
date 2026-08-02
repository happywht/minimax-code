/**
 * Memory store — long-term memory list/search/add/delete for the
 * Settings page's Memory tab.
 *
 * Wire contract (`handlers_memory.py`):
 *   memory.list    -> { memories, total }
 *   memory.search  -> { memories, total }
 *   memory.add     -> { memory }
 *   memory.delete  -> { ok, id }
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { MemoryCategory, MemoryEntry } from "../types/ipc";

export type MemoryEntryItem = MemoryEntry;

export interface MemoryState {
  memories: MemoryEntryItem[];
  total: number;
  loading: boolean;
  searchQuery: string;

  refresh: (opts?: {
    project_id?: string;
    session_id?: string;
    category?: MemoryCategory;
    limit?: number;
    offset?: number;
  }) => Promise<void>;
  search: (
    query: string,
    opts?: { project_id?: string; session_id?: string; category?: MemoryCategory; limit?: number },
  ) => Promise<void>;
  add: (opts: {
    content: string;
    category?: MemoryCategory;
    confidence?: number;
    project_id?: string;
    session_id?: string;
    source?: string;
  }) => Promise<MemoryEntryItem | null>;
  remove: (id: string) => Promise<void>;
  setSearchQuery: (query: string) => void;
}

export const useMemoryStore = create<MemoryState>((set, get) => ({
  memories: [],
  total: 0,
  loading: false,
  searchQuery: "",

  refresh: async (opts) => {
    set({ loading: true });
    try {
      const r = await typedIPC.listMemories(opts ?? {});
      set({ memories: r.memories, total: r.total, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load memories", message);
    }
  },

  search: async (query, opts) => {
    set({ loading: true, searchQuery: query });
    try {
      const r = query.trim()
        ? await typedIPC.searchMemories(query.trim(), opts ?? {})
        : await typedIPC.listMemories(opts ?? {});
      set({ memories: r.memories, total: r.total, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to search memories", message);
    }
  },

  add: async (opts) => {
    try {
      const r = await typedIPC.addMemory(opts);
      set((s) => ({
        memories: [r.memory, ...s.memories],
        total: s.total + 1,
      }));
      return r.memory;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to add memory", message);
      return null;
    }
  },

  remove: async (id) => {
    const prev = get().memories;
    set((s) => ({
      memories: s.memories.filter((m) => m.id !== id),
      total: Math.max(0, s.total - 1),
    }));
    try {
      await typedIPC.deleteMemory(id);
    } catch (err) {
      set({ memories: prev, total: prev.length });
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to delete memory", message);
    }
  },

  setSearchQuery: (query) => set({ searchQuery: query }),
}));
