/**
 * A single navigation row in the sidebar. Shows an icon, a label, an
 * optional count badge, and a "selected" state.
 */
import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

export interface NavItemProps {
  icon: ReactNode;
  label: string;
  selected?: boolean;
  count?: number;
  onClick?: () => void;
  testId?: string;
  trailing?: ReactNode;
}

export function NavItem({
  icon,
  label,
  selected = false,
  count,
  onClick,
  testId,
  trailing,
}: NavItemProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      data-selected={selected ? "true" : "false"}
      className={
        "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors " +
        (selected
          ? "bg-minimax-accent/15 text-minimax-fg"
          : "text-minimax-fg/80 hover:bg-minimax-border/60 hover:text-minimax-fg")
      }
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-minimax-muted group-hover:text-minimax-fg">
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {count != null && count > 0 && (
        <span className="ml-auto rounded bg-minimax-border px-1.5 py-0.5 text-[10px] font-medium text-minimax-muted">
          {count}
        </span>
      )}
      {trailing}
      {selected && (
        <ChevronRight size={12} className="ml-1 text-minimax-accent" />
      )}
    </button>
  );
}
