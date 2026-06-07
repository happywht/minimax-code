/**
 * NotificationBell — bell icon with unread badge for the TopBar.
 *
 * Toggles the ``NotificationCenter`` dropdown panel.
 *
 * v0.7.0 — Mobile Connectivity Enhancement
 */
import { useRef, useEffect } from "react";
import { Bell } from "lucide-react";
import { useNotificationStore } from "@/stores/notificationStore";
import { NotificationCenter } from "./NotificationCenter";

export function NotificationBell(): JSX.Element {
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const open = useNotificationStore((s) => s.open);
  const toggleOpen = useNotificationStore((s) => s.toggleOpen);
  const setOpen = useNotificationStore((s) => s.setOpen);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, setOpen]);

  return (
    <div ref={ref} className="relative" data-testid="notification-bell">
      <button
        type="button"
        onClick={toggleOpen}
        className="relative flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
        data-testid="notification-bell-btn"
      >
        <Bell size={14} />
        {unreadCount > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white"
            data-testid="notification-badge"
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1">
          <NotificationCenter />
        </div>
      )}
    </div>
  );
}
