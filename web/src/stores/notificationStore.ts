/**
 * NotificationStore — manages the notification centre state.
 *
 * Subscribes to ``notification.new`` / ``notification.read`` WebSocket
 * events so the bell badge updates in real time.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { create } from "zustand";
import { typedIPC, ipc } from "../ipc/client";
import type { NotificationEntry, ListNotificationsResult } from "../types/ipc";
import { toast } from "../components/ErrorBoundary";
import { trimArray, MAX_NOTIFICATIONS } from "../lib/eviction";

// Re-export for convenience
export type { NotificationEntry, ListNotificationsResult };

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface NotificationState {
  entries: NotificationEntry[];
  total: number;
  unreadCount: number;
  loading: boolean;
  error: string | null;
  open: boolean;

  refresh(opts?: { limit?: number; offset?: number; unread_only?: boolean }): Promise<void>;
  markRead(id: string): Promise<void>;
  markAllRead(): Promise<void>;
  deleteNotification(id: string): Promise<void>;
  purge(beforeIso: string, readOnly?: boolean): Promise<void>;
  setOpen(v: boolean): void;
  toggleOpen(): void;

  /** Wire up WS listeners — call once at app boot. */
  _init(): () => void;
}

export const useNotificationStore = create<NotificationState>((set, get) => {
  let _unsub: (() => void) | null = null;

  return {
    entries: [],
    total: 0,
    unreadCount: 0,
    loading: false,
    error: null,
    open: false,

    async refresh(opts) {
      set({ loading: true, error: null });
      try {
        const res = await typedIPC.listNotifications(opts ?? {});
        set({
          entries: trimArray(res.entries, MAX_NOTIFICATIONS),
          total: res.total,
          unreadCount: res.entries.filter((e) => !e.read).length,
        });
      } catch (err: unknown) {
        const msg = String(err);
        toast.error("Failed to load notifications", msg);
        set({ error: msg });
      } finally {
        set({ loading: false });
      }
    },

    async markRead(id) {
      try {
        const entry = await typedIPC.markNotificationRead(id);
        set((s) => ({
          entries: s.entries.map((e) => (e.id === id ? entry : e)),
          unreadCount: Math.max(0, s.unreadCount - 1),
        }));
      } catch (err: unknown) {
        const msg = String(err);
        toast.error("Failed to mark notification as read", msg);
        set({ error: msg });
      }
    },

    async markAllRead() {
      try {
        const _marked = (await typedIPC.markAllNotificationsRead()) as { marked: number };
        void _marked;
        set((s) => ({
          entries: s.entries.map((e) => ({ ...e, read: true })),
          unreadCount: 0,
        }));
      } catch (err: unknown) {
        const msg = String(err);
        toast.error("Failed to mark all notifications as read", msg);
        set({ error: msg });
      }
    },

    async deleteNotification(id) {
      try {
        await typedIPC.deleteNotification(id);
        set((s) => {
          const entry = s.entries.find((e) => e.id === id);
          return {
            entries: s.entries.filter((e) => e.id !== id),
            total: s.total - 1,
            unreadCount: entry && !entry.read ? Math.max(0, s.unreadCount - 1) : s.unreadCount,
          };
        });
      } catch (err: unknown) {
        const msg = String(err);
        toast.error("Failed to delete notification", msg);
        set({ error: msg });
      }
    },

    async purge(beforeIso, readOnly) {
      try {
        const _purged = (await typedIPC.purgeNotifications(beforeIso, readOnly)) as { purged: number };
        void _purged;
        // Refresh to get accurate state after purge
        await get().refresh();
      } catch (err: unknown) {
        const msg = String(err);
        toast.error("Failed to purge notifications", msg);
        set({ error: msg });
      }
    },

    setOpen(v) {
      set({ open: v });
    },

    toggleOpen() {
      set((s) => ({ open: !s.open }));
    },

    _init() {
      // Fetch initial data
      get().refresh();

      // P0 fix: ipc.on() requires (event, callback) — was called with a
      // single function that never matched the signature, so real-time
      // notifications were completely broken.  Split into two typed
      // subscriptions; each returns an unsubscribe function.
      const unsubNew = ipc.on<NotificationEntry>("notification.new", (env) => {
        const entry = env.data;
        if (!entry) return;
        set((s) => ({
          entries: trimArray([entry, ...s.entries], MAX_NOTIFICATIONS),
          total: s.total + 1,
          unreadCount: s.unreadCount + (entry.read ? 0 : 1),
        }));
      });

      const unsubRead = ipc.on<NotificationEntry>("notification.read", (env) => {
        const entry = env.data;
        if (!entry) return;
        set((s) => {
          const prev = s.entries.find((e) => e.id === entry.id);
          const wasUnread = prev && !prev.read;
          return {
            entries: s.entries.map((e) => (e.id === entry.id ? { ...e, read: true } : e)),
            unreadCount: wasUnread ? Math.max(0, s.unreadCount - 1) : s.unreadCount,
          };
        });
      });

      _unsub = () => { unsubNew(); unsubRead(); };
      return _unsub;
    },
  };
});

/** Convenience: initialise once at app boot. Returns cleanup fn. */
export function initNotificationStore(): () => void {
  return useNotificationStore.getState()._init();
}
