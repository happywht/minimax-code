/**
 * NotificationCenter — dropdown panel listing recent notifications.
 *
 * Rendered inside ``NotificationBell`` as a floating panel.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { useEffect } from "react";
import {
  Check,
  Info,
  AlertTriangle,
  XCircle,
  Webhook,
  Shield,
  Workflow,
  X,
  Trash2,
} from "lucide-react";
import { Panel } from "../../ui/Panel";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { EmptyState } from "../../ui/EmptyState";
import { strings } from "../../ui/strings";
import { SkeletonTable } from "./Skeleton";
import { useNotificationStore } from "../../stores/notificationStore";
import type { NotificationEntry } from "../../stores/notificationStore";
import { formatRelative } from "../../lib/time";
import { requestConfirmation } from "../modals/ConfirmationDialog";

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
  const purge = useNotificationStore((s) => s.purge);

  useEffect(() => {
    refresh({ limit: 50 });
  }, [refresh]);

  const hasReadEntries = entries.some((e) => e.read);

  const handlePurgeRead = async () => {
    const accepted = await requestConfirmation({
      title: strings.layout.notifications.purgeReadTitle,
      description: strings.layout.notifications.purgeReadDesc,
      confirmLabel: strings.layout.notifications.purgeReadLabel,
    });
    if (!accepted) return;
    // Purge everything already read ("before now", read-only); the store
    // refreshes the list itself and toasts on failure.
    await purge(new Date().toISOString(), true);
  };

  return (
    <Panel
      data-testid="notification-center"
      title={
        <span className="normal-case">
          {strings.layout.notifications.title}
          {unreadCount > 0 && (
            <span className="ml-1.5 text-ink-1">
              {strings.layout.notifications.unreadSuffix(unreadCount)}
            </span>
          )}
        </span>
      }
      actions={
        <>
          {unreadCount > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              icon={<Check size={12} />}
              onClick={() => markAllRead()}
              title={strings.layout.notifications.markAllReadTitle}
            >
              {strings.layout.notifications.readAll}
            </Button>
          )}
          {hasReadEntries && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              icon={<Trash2 size={12} />}
              onClick={() => void handlePurgeRead()}
              data-testid="notification-purge-read"
            >
              {strings.layout.notifications.purgeRead}
            </Button>
          )}
          <IconButton
            aria-label={strings.layout.notifications.close}
            size="sm"
            onClick={() => setOpen(false)}
          >
            <X size={12} />
          </IconButton>
        </>
      }
      className="w-80 shadow-pop"
      flush
    >
      <div className="max-h-80 overflow-y-auto">
        {loading && entries.length === 0 && (
          <div className="p-3">
            <SkeletonTable rows={3} />
          </div>
        )}
        {!loading && entries.length === 0 && (
          <EmptyState
            title="暂无通知"
            icon={<Info size={24} />}
            testId="notification-center-empty"
          />
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
    </Panel>
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
      className={`group flex items-start gap-2 border-b border-line/50 px-3 py-2 text-xs transition-colors hover:bg-surface-3 ${
        entry.read ? "opacity-60" : ""
      }`}
      data-testid={`notification-item-${entry.id}`}
    >
      <Icon size={14} className="mt-0.5 shrink-0 text-ink-2" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <span className="font-medium leading-tight text-ink-0">
            {entry.title}
          </span>
          <span className="shrink-0 text-[11px] text-ink-1">
            {formatRelative(entry.created_at)}
          </span>
        </div>
        {entry.body && (
          <p className="mt-0.5 leading-snug line-clamp-2 text-ink-1">
            {entry.body}
          </p>
        )}
        <div className="mt-1 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
          {!entry.read && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onMarkRead(entry.id)}
              className="h-auto px-0 py-0 text-[11px] hover:underline"
            >
              {strings.layout.notifications.markRead}
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onDelete(entry.id)}
            className="h-auto px-0 py-0 text-[11px] text-status-error hover:bg-transparent hover:text-status-error hover:underline"
          >
            {strings.layout.notifications.delete}
          </Button>
        </div>
      </div>
      {!entry.read && (
        <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-accent" />
      )}
    </div>
  );
}
