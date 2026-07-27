/**
 * NotificationBell — bell icon with unread badge for the TopBar.
 *
 * Toggles the ``NotificationCenter`` dropdown panel.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { useRef, useCallback } from "react";
import { Bell } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { Badge } from "../../ui/Badge";
import { useNotificationStore } from "../../stores/notificationStore";
import { NotificationCenter } from "./NotificationCenter";
import { useClickOutside } from "../../lib/useClickOutside";

export function NotificationBell(): JSX.Element {
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const open = useNotificationStore((s) => s.open);
  const toggleOpen = useNotificationStore((s) => s.toggleOpen);
  const setOpen = useNotificationStore((s) => s.setOpen);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click — shared hook replaces inline mousedown listener.
  const closePanel = useCallback(() => setOpen(false), [setOpen]);
  useClickOutside(ref, closePanel, { enabled: open });

  return (
    <div ref={ref} className="relative" data-testid="notification-bell">
      <IconButton
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
        title="Notifications"
        data-testid="notification-bell-btn"
        onClick={toggleOpen}
        className="relative"
      >
        <Bell size={14} />
        {unreadCount > 0 && (
          <Badge
            tone="error"
            data-testid="notification-badge"
            className="absolute -right-0.5 -top-0.5 h-4 min-w-4 justify-center border-0 bg-status-error px-1 py-0 text-[11px] font-bold text-white"
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </Badge>
        )}
      </IconButton>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1">
          <NotificationCenter />
        </div>
      )}
    </div>
  );
}
