/**
 * IconButton — square, icon-only action for toolbars and headers.
 * Always requires an `aria-label` (enforced by types).
 */
import { forwardRef, type ButtonHTMLAttributes } from "react";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  "aria-label": string;
  /** 28px default; `sm` is 24px. */
  size?: "sm" | "md";
  /** Highlight as toggled/active. */
  active?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    { size = "md", active = false, className = "", children, type = "button", ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        className={
          "inline-flex shrink-0 items-center justify-center rounded-md transition-colors duration-150 " +
          "focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 " +
          (size === "sm" ? "h-6 w-6 " : "h-7 w-7 ") +
          (active
            ? "bg-accent-subtle text-accent "
            : "text-ink-1 hover:bg-surface-3 hover:text-ink-0 ") +
          "[&>svg]:h-3.5 [&>svg]:w-3.5 " +
          className
        }
        {...rest}
      >
        {children}
      </button>
    );
  },
);
