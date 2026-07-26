/**
 * Input / Textarea — shared form field styling.
 */
import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";

const FIELD_CLASSES =
  "w-full rounded-md border border-line bg-surface-2 text-ink-0 outline-none " +
  "transition-colors duration-150 placeholder:text-ink-2 " +
  "hover:border-line-strong focus:border-accent/60 " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 28px dense default; `md` is 32px. */
  fieldSize?: "sm" | "md";
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { fieldSize = "sm", className = "", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={
        FIELD_CLASSES +
        (fieldSize === "sm" ? " h-7 px-2 text-xs " : " h-8 px-2.5 text-sm ") +
        className
      }
      {...rest}
    />
  );
});

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className = "", ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={FIELD_CLASSES + " min-h-[72px] px-2.5 py-2 text-sm " + className}
      {...rest}
    />
  );
});
