/**
 * Badge — small status/label pill.
 */
import type { HTMLAttributes, ReactNode } from "react";

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "error" | "info";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Optional leading dot. */
  dot?: boolean;
  children: ReactNode;
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "border-line bg-surface-3 text-ink-1",
  accent: "border-accent/30 bg-accent-subtle text-accent",
  success: "border-status-success/30 bg-[var(--status-success-subtle)] text-status-success",
  warning: "border-status-warning/30 bg-[var(--status-warning-subtle)] text-status-warning",
  error: "border-status-error/30 bg-[var(--status-error-subtle)] text-status-error",
  info: "border-status-info/30 bg-[var(--status-info-subtle)] text-status-info",
};

export function Badge({
  tone = "neutral",
  dot = false,
  className = "",
  children,
  ...rest
}: BadgeProps): JSX.Element {
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 " +
        "text-[11px] font-medium leading-none " +
        `${TONE_CLASSES[tone]} ${className}`
      }
      {...rest}
    >
      {dot && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
