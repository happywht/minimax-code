/**
 * EmptyState — friendly placeholder for empty lists/panels.
 */
import type { ReactNode } from "react";

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
  testId?: string;
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
  testId,
}: EmptyStateProps): JSX.Element {
  return (
    <div
      data-testid={testId}
      className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 px-4 py-6 text-center"
    >
      {icon != null && <div className="text-ink-2 [&>svg]:h-6 [&>svg]:w-6">{icon}</div>}
      <p className="text-sm text-ink-1">{title}</p>
      {hint != null && <p className="max-w-[240px] text-xs text-ink-2">{hint}</p>}
      {action != null && <div className="mt-1">{action}</div>}
    </div>
  );
}
