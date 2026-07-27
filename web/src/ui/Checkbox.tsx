/**
 * Checkbox — a tiny styled native checkbox used in lists and toolbars.
 */
import { forwardRef, type InputHTMLAttributes } from "react";

export type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { className = "", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      type="checkbox"
      className={[
        "h-3.5 w-3.5 cursor-pointer rounded border-line bg-surface-2 text-accent",
        "transition-colors duration-150",
        "checked:border-accent checked:bg-accent",
        "focus:outline-none focus:ring-2 focus:ring-accent/40",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      ].join(" ")}
      {...rest}
    />
  );
});
