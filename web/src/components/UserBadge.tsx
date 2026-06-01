/**
 * User badge — bottom-left avatar + plan info, matching the layout
 * sketch.
 */
import { useState } from "react";
import { Crown, LogOut, Settings } from "lucide-react";

export interface UserBadgeProps {
  name?: string;
  email?: string;
  plan?: string;
  onSignOut?: () => void;
  onSettings?: () => void;
}

export function UserBadge({
  name = "User",
  email = "user@example.com",
  plan = "Max Plan",
  onSignOut,
  onSettings,
}: UserBadgeProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const initials = name
    .split(/\s+/)
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="relative" data-testid="user-badge">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-minimax-border/60"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-minimax-accent/30 text-[11px] font-semibold text-minimax-fg">
          {initials || "U"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-minimax-fg">
            {name}
          </span>
          <span className="flex items-center gap-1 truncate text-[10px] text-minimax-muted">
            <Crown size={10} className="text-amber-400" />
            {plan}
          </span>
        </span>
      </button>
      {open && (
        <div
          className="absolute bottom-full left-0 mb-1 w-56 rounded-md border border-minimax-border bg-minimax-panel p-1 shadow-xl"
          role="menu"
        >
          <div className="px-2 py-1.5">
            <div className="truncate text-xs text-minimax-fg">{name}</div>
            <div className="truncate text-[10px] text-minimax-muted">
              {email}
            </div>
          </div>
          <hr className="border-minimax-border" />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onSettings?.();
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-minimax-fg hover:bg-minimax-border"
          >
            <Settings size={12} /> Settings
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onSignOut?.();
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-red-300 hover:bg-minimax-border"
          >
            <LogOut size={12} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
