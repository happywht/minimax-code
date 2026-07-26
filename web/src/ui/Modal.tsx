/**
 * Modal — accessible dialog: backdrop, Escape-to-close, focus trap,
 * and entrance animation.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { useFocusTrap } from "../lib/useFocusTrap";
import { IconButton } from "./IconButton";

export interface ModalProps {
  /** Dialog title shown in the header. */
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Optional footer (buttons row). */
  footer?: ReactNode;
  /** Max-width utility class, e.g. "max-w-lg". */
  widthClass?: string;
  /** Use `fixed inset-0` (viewport) instead of `absolute inset-0`. */
  fixed?: boolean;
  testId?: string;
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  widthClass = "max-w-lg",
  fixed = true,
  testId,
}: ModalProps): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className={
        (fixed ? "fixed" : "absolute") +
        " inset-0 z-50 flex items-center justify-center bg-surface-overlay p-4 backdrop-blur-sm"
      }
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        data-testid={testId}
        className={
          "animate-modal-in flex max-h-[85vh] w-full flex-col overflow-hidden rounded-xl " +
          "border border-line bg-surface-1 shadow-modal " +
          widthClass
        }
      >
        <header className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-line px-4">
          <h2 className="truncate text-sm font-semibold text-ink-0">{title}</h2>
          <IconButton
            aria-label="Close dialog"
            onClick={onClose}
            data-testid={testId ? `${testId}-close` : undefined}
          >
            <X />
          </IconButton>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>
        {footer != null && (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-line px-4 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
