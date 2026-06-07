/**
 * NotificationCenter — dropdown panel listing recent notifications.
 *
 * Rendered inside ``NotificationBell`` as a floating panel.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { useEffect } from "react";
import { Check, Trash2, Info, AlertTriangle, XCircle, Webhook, Shield, Workflow, X } from "lucide-react";
import { SkeletonTable } from "./Skeleton";
import { useNotificationStore } from "../stores/notificationStore";
import type { NotificationEntry } from "../stores/notificationStore";
import { formatRelative } from "../lib/time";

const TYPE_ICON: Record<string, typeof Info> = {
  info: Info,
  warning: AlertTriangle,
  error: XCircle,
  webhook: Webhook,
  permission: Shield,
  workflow: Workflow,
};

function typeIcon(type: string) {
  return TYPE_ICON[type] ?? Info;
}

export function NotificationCenter(): JSX.Element {
  const entries = useNotificationStore((s) => s.entries);
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const loading = useNotificationStore((s) => s.loading);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const deleteNotification = useNotificationStore((s) => s.deleteNotification);
  const setOpen = useNotificationStore((s) => s.setOpen);
  const refresh = useNotificationStore((s) => s.refresh);

  useEffect(() => {
    refresh({ limit: 50 });
  }, [refresh]);

  return (
    <div
      className="w-80 rounded-lg border border-minimax-border bg-minimax-panel shadow-xl"
      data-testid="notification-center"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
        <span className="text-xs font-semibold text-minimax-fg">
          Notifications
          {unreadCount > 0 && (
            <span className="ml-1.5 text-minimax-muted">({unreadCount} unread)</span>
          )}
        </span>
        <div className="flex items-center gap-1">
          {unreadCount > 0 && (
            <button
              type="button"
              onClick={() => markAllRead()}
              className="rounded px-1.5 py-0.5 text-[11px] text-minimax-accent hover:bg-minimax-border"
              title="Mark all as read"
            >
              <Check size={12} className="inline -mt-px mr-0.5" />
              Read all
            </button>
          )}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          >
            <X size={12} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="max-h-80 overflow-y-auto">
        {loading && entries.length === 0 && (
          <div className="p-3">
            <SkeletonTable rows={3} />
          </div>
        )}
        {!loading && entries.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-minimax-muted">
            No notifications yet
          </div>
        )}
        {entries.map((entry) => (
          <NotificationItem
            key={entry.id}
            entry={entry}
            onMarkRead={markRead}
            onDelete={deleteNotification}
          />
        ))}
      </div>
    </div>
  );
}

function NotificationItem({
  entry,
  onMarkRead,
  onDelete,
}: {
  entry: NotificationEntry;
  onMarkRead: (id: string) => void;
  onDelete: (id: string) => void;
}): JSX.Element {
  const Icon = typeIcon(entry.type);
  return (
    <div
      className={`group flex items-start gap-2 border-b border-minimax-border/50 px-3 py-2 text-xs transition-colors hover:bg-minimax-border/30 ${
        entry.read ? "opacity-60" : ""
      }`}
      data-testid={`notification-item-${entry.id}`}
    >
      <Icon size={14} className="mt-0.5 shrink-0 text-minimax-muted" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <span className="font-medium text-minimax-fg leading-tight">
            {entry.title}
          </span>
          <span className="shrink-0 text-[11px] text-minimax-muted">
            {formatRelative(entry.created_at)}
          </span>
        </div>
        {entry.body && (
          <p className="mt-0.5 text-minimax-muted leading-snug line-clamp-2">
            {entry.body}
          </p>
        )}
        <div className="mt-1 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          {!entry.read && (
            <button
              type="button"
              onClick={() => onMarkRead(entry.id)}
              className="text-[11px] text-minimax-accent hover:underline"
            >
              Mark read
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(entry.id)}
            className="text-[11px] text-red-400 hover:underline"
          >
            Delete
          </button>
        </div>
      </div>
      {!entry.read && (
        <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-minimax-accent" />
      )}
    </div>
  );
}
