/**
 * useFocusTrap — traps keyboard focus within a container element.
 *
 * When `active` is true:
 *   1. The previously-focused element is remembered.
 *   2. Focus moves to the first focusable child of `containerRef`.
 *   3. Tab / Shift+Tab cycle only within the container.
 *   4. On deactivation, focus is restored to the remembered element.
 *
 * Usage:
 *   const ref = useRef<HTMLDivElement>(null);
 *   useFocusTrap(ref, isOpen);
 *   return <div ref={ref}>...</div>;
 */
import { useEffect, useRef } from "react";

/** Selector for elements that can receive keyboard focus. */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

function getFocusableChildren(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

export function useFocusTrap(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
): void {
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active || !containerRef.current) return;

    // Remember the currently focused element (the trigger button)
    previouslyFocusedRef.current = document.activeElement as HTMLElement;

    // Move focus into the container
    const container = containerRef.current;
    const focusable = getFocusableChildren(container);
    if (focusable.length > 0) {
      focusable[0].focus();
    } else {
      // No focusable child — make the container itself focusable
      container.setAttribute("tabindex", "-1");
      container.focus();
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;

      const currentFocusable = getFocusableChildren(container);
      if (currentFocusable.length === 0) {
        e.preventDefault();
        return;
      }

      const first = currentFocusable[0];
      const last = currentFocusable[currentFocusable.length - 1];

      if (e.shiftKey) {
        // Shift+Tab: if at first element, wrap to last
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        // Tab: if at last element, wrap to first
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    container.addEventListener("keydown", onKeyDown);

    return () => {
      container.removeEventListener("keydown", onKeyDown);
      // Restore focus to the trigger element
      if (previouslyFocusedRef.current && previouslyFocusedRef.current.focus) {
        previouslyFocusedRef.current.focus();
      }
      previouslyFocusedRef.current = null;
      // Clean up tabindex if we added it
      if (container.getAttribute("tabindex") === "-1") {
        container.removeAttribute("tabindex");
      }
    };
  }, [active, containerRef]);
}
