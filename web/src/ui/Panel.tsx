/**
 * Panel — the standard card container: rounded, bordered surface-2
 * with an optional header row (title + actions).
 */
import type { HTMLAttributes, ReactNode } from "react";

export interface PanelProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Header title. When provided, a header row is rendered. */
  title?: ReactNode;
  /** Right-aligned header actions. */
  actions?: ReactNode;
  /** Remove default body padding (e.g. for lists). */
  flush?: boolean;
  children: ReactNode;
}

export function Panel({
  title,
  actions,
  flush = false,
  className = "",
  children,
  ...rest
}: PanelProps): JSX.Element {
  return (
    <section
      className={
        "flex min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-surface-2 " +
        className
      }
      {...rest}
    >
      {(title != null || actions != null) && (
        <header className="flex h-9 shrink-0 items-center justify-between gap-2 border-b border-line px-3">
          <h3 className="truncate text-xs font-semibold uppercase tracking-wide text-ink-1">
            {title}
          </h3>
          {actions != null && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={"min-h-0 flex-1 " + (flush ? "" : "p-3")}>{children}</div>
    </section>
  );
}
