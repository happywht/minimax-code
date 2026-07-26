/**
 * Button — the single source of truth for clickable actions.
 *
 * Variants:
 *   primary   — accent fill, for the one main action in a view
 *   secondary — bordered, neutral surface
 *   ghost     — borderless, low-emphasis (toolbars, rows)
 *   danger    — destructive actions
 *   subtle    — tinted accent background, medium emphasis
 *
 * Sizes: `sm` (28px) for dense chrome, `md` (32px) default.
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Leading icon (rendered at 14px box). */
  icon?: ReactNode;
  /** Show a spinner and disable the button while busy. */
  loading?: boolean;
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "h-7 gap-1.5 rounded-md px-2.5 text-xs",
  md: "h-8 gap-2 rounded-lg px-3 text-sm",
};

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-contrast font-medium hover:bg-accent-hover active:bg-accent-active " +
    "disabled:bg-accent/50 disabled:text-accent-contrast/70",
  secondary:
    "border border-line bg-surface-2 text-ink-0 hover:border-line-strong hover:bg-surface-3 " +
    "disabled:opacity-50",
  ghost:
    "text-ink-1 hover:bg-surface-3 hover:text-ink-0 disabled:opacity-50",
  danger:
    "border border-status-error/40 bg-[var(--status-error-subtle)] text-status-error " +
    "hover:bg-status-error/20 disabled:opacity-50",
  subtle:
    "bg-accent-subtle text-accent hover:bg-accent/20 disabled:opacity-50",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    icon,
    loading = false,
    disabled,
    className = "",
    children,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={
        "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap " +
        "transition-colors duration-150 focus-visible:outline-none " +
        "disabled:cursor-not-allowed " +
        `${SIZE_CLASSES[size]} ${VARIANT_CLASSES[variant]} ${className}`
      }
      {...rest}
    >
      {loading ? (
        <Spinner size={size === "sm" ? 12 : 14} />
      ) : (
        icon && <span className="flex shrink-0 items-center [&>svg]:h-3.5 [&>svg]:w-3.5">{icon}</span>
      )}
      {children}
    </button>
  );
});
