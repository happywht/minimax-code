/**
 * Audit store — drives the Settings page's Audit tab.
 *
 * Provides paginated access to the tool-call audit trail and
 * aggregate stats. The ``refresh`` method fetches the latest page;
 * ``loadStats`` fetches the aggregate dashboard numbers.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc/client";
import type { AuditEntry, AuditStats } from "../types/ipc";

export interface AuditState {
  entries: AuditEntry[];
  total: number;
  stats: AuditStats | null;
  loading: boolean;
  error: string | null;
  /** Current page (0-based). */
  page: number;
  /** Items per page. */
  pageSize: number;
  /** Active tool_name filter (null = all). */
  filterTool: string | null;

  refresh: () => Promise<void>;
  loadStats: () => Promise<void>;
  setPage: (page: number) => void;
  setFilterTool: (tool: string | null) => void;
  purge: (beforeIso: string) => Promise<number>;
}

export const useAuditStore = create<AuditState>((set, get) => ({
  entries: [],
  total: 0,
  stats: null,
  loading: false,
  error: null,
  page: 0,
  pageSize: 50,
  filterTool: null,

  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const { page, pageSize, filterTool } = get();
      const result = await typedIPC.listAudit({
        limit: pageSize,
        offset: page * pageSize,
        tool_name: filterTool ?? undefined,
      });
      set({ entries: result.entries, total: result.total, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  loadStats: async () => {
    try {
      const stats = await typedIPC.auditStats();
      set({ stats });
    } catch {
      // Stats are best-effort — don't block the UI.
    }
  },

  setPage: (page: number) => {
    set({ page });
    get().refresh();
  },

  setFilterTool: (tool: string | null) => {
    set({ filterTool: tool, page: 0 });
    get().refresh();
  },

  purge: async (beforeIso: string) => {
    try {
      const result = await typedIPC.purgeAudit(beforeIso);
      await get().refresh();
      await get().loadStats();
      return result.deleted;
    } catch (e) {
      set({ error: String(e) });
      return 0;
    }
  },
}));
