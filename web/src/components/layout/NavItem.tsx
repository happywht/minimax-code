/**
 * A single navigation row in the sidebar. Shows an icon, a label, an
 * optional count badge, and a "selected" state with an accent rail.
 */
import type { ReactNode } from "react";

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
      aria-current={selected ? "page" : undefined}
      className={
        "group relative flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors duration-150 " +
        (selected
          ? "bg-accent-subtle text-ink-0"
          : "text-ink-1 hover:bg-surface-3 hover:text-ink-0")
      }
    >
      {/* Accent rail on the selected item */}
      <span
        aria-hidden
        className={
          "absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-accent transition-opacity duration-150 " +
          (selected ? "opacity-100" : "opacity-0")
        }
      />
      <span
        className={
          "flex h-4 w-4 shrink-0 items-center justify-center transition-colors duration-150 " +
          (selected ? "text-accent" : "text-ink-2 group-hover:text-ink-1")
        }
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {count != null && count > 0 && (
        <span className="ml-auto rounded-full bg-surface-3 px-1.5 py-0.5 text-[11px] font-medium leading-none text-ink-1">
          {count}
        </span>
      )}
      {trailing}
    </button>
  );
}
