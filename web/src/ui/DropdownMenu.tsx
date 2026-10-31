/**
 * DropdownMenu — a small accessible menu triggered by a button.
 *
 * - Toggle on click, close on outside click or Escape.
 * - Focus trap cycles through menu items.
 * - Menu items are plain buttons; callers provide the trigger element.
 */
import { cloneElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { useFocusTrap } from "../lib/useFocusTrap";

export interface DropdownMenuItem {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

export interface DropdownMenuProps {
  /** The element that opens the menu. Must be a single focusable button-like element. */
  trigger: ReactElement;
  items: DropdownMenuItem[];
  /** Horizontal alignment of the menu relative to the trigger wrapper. */
  align?: "left" | "right";
  testId?: string;
}

export function DropdownMenu({
  trigger,
  items,
  align = "right",
  testId,
}: DropdownMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useFocusTrap(menuRef, open);

  useEffect(() => {
    if (!open) return;
    const onDocumentClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!wrapperRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocumentClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocumentClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const handleItemClick = (item: DropdownMenuItem) => {
    if (item.disabled) return;
    item.onClick();
    setOpen(false);
  };

  const triggerElement = cloneElement(trigger, {
    "aria-haspopup": "menu",
    "aria-expanded": open,
    "data-testid": testId,
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      setOpen((v) => !v);
      trigger.props.onClick?.(e);
    },
  });

  return (
    <div ref={wrapperRef} className="relative inline-flex">
      {triggerElement}
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-orientation="vertical"
          onKeyDown={(event) => {
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
            const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)'));
            if (!buttons.length) return;
            const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
            const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
              event.key === "ArrowDown" ? (current + 1) % buttons.length : (current < 0 ? buttons.length - 1 : (current - 1 + buttons.length) % buttons.length);
            event.preventDefault(); buttons[index].focus();
          }}
          className={[
            "absolute top-full z-50 mt-1 max-h-[min(60vh,24rem)] min-w-[9rem] max-w-[min(90vw,24rem)] overflow-y-auto rounded-md border border-line",
            "bg-surface-1 py-1 shadow-lg",
            align === "right" ? "right-0" : "left-0",
          ].join(" ")}
          data-testid={testId ? `${testId}-menu` : undefined}
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => handleItemClick(item)}
              className={[
                "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px]",
                "transition-colors duration-150 focus-visible:bg-accent-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-accent",
                "disabled:cursor-not-allowed disabled:opacity-50",
                item.danger
                  ? "text-status-error hover:bg-status-error/10"
                  : "text-ink-0 hover:bg-surface-2",
              ].join(" ")}
            >
              {item.icon && <span className="flex shrink-0 items-center [&>svg]:h-3.5 [&>svg]:w-3.5">{item.icon}</span>}
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
