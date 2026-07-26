/**
 * Shared form chrome for settings tabs.
 *
 * Keeps the label + control + hint rhythm, section headers, and
 * <select> styling consistent across every tab. Built on the
 * design-system tokens (ink/line/surface/accent).
 */
import type { ReactNode, SelectHTMLAttributes } from "react";

export interface TabHeaderProps {
  title: string;
  hint?: ReactNode;
  /** Right-aligned actions (usually a single primary/subtle Button). */
  action?: ReactNode;
}

/** Section heading row used at the top of each settings tab. */
export function TabHeader({ title, hint, action }: TabHeaderProps): JSX.Element {
  return (
    <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink-0">{title}</h2>
        {hint != null && <p className="mt-0.5 text-pretty text-[11px] text-ink-2">{hint}</p>}
      </div>
      {action != null && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}

export interface FieldProps {
  label: ReactNode;
  htmlFor: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}

/** Label + control (+ optional hint) form row. */
export function Field({ label, htmlFor, hint, className = "", children }: FieldProps): JSX.Element {
  return (
    <div className={"min-w-0 " + className}>
      <label htmlFor={htmlFor} className="mb-0.5 block text-[11px] text-ink-2">
        {label}
      </label>
      {children}
      {hint != null && <p className="mt-0.5 text-[11px] text-ink-2">{hint}</p>}
    </div>
  );
}

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

/** Styled <select> matching the Input primitive (28px dense). */
export function Select({ className = "", ...rest }: SelectProps): JSX.Element {
  return (
    <select
      className={
        "h-7 w-full rounded-md border border-line bg-surface-2 px-2 text-xs text-ink-0 " +
        "outline-none transition-colors duration-150 hover:border-line-strong " +
        "focus:border-accent/60 disabled:cursor-not-allowed disabled:opacity-50 " +
        className
      }
      {...rest}
    />
  );
}

/** Inline code chip used inside settings copy. */
export function InlineCode({ children }: { children: ReactNode }): JSX.Element {
  return (
    <code className="mx-0.5 rounded bg-surface-3 px-1 py-0.5 font-mono text-[11px] text-ink-1">
      {children}
    </code>
  );
}

/** Standard error banner for store-level errors. */
export function ErrorBanner({ message }: { message: string }): JSX.Element {
  return (
    <div className="rounded-md border border-status-error/40 bg-[var(--status-error-subtle)] px-3 py-2 text-xs text-status-error">
      {message}
    </div>
  );
}
