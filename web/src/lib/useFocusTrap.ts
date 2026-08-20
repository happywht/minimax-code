/**
 * useFocusTrap — traps keyboard focus within a container element.
 *
 * When `active` is true:
 *   1. The previously-focused element is remembered.
 *   2. Focus moves to the first focusable child of `containerRef`
 *      (an `[autofocus]` child wins over the first focusable).
 *   3. Tab / Shift+Tab cycle only within the container.
 *   4. If focus escapes the container (programmatic focus, browser
 *      quirks), the next Tab keystroke pulls it back in.
 *   5. On deactivation, focus is restored to the remembered element.
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
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * Filter out elements that match the selector but cannot actually be
 * focused: hidden via the `hidden` attribute, `aria-hidden="true"`, or
 * layout-hidden (`display:none` etc. via `checkVisibility`, when the
 * runtime supports it — jsdom has no layout, so it opts out and tests
 * rely on the explicit attribute checks instead).
 */
function isActuallyFocusable(element: HTMLElement): boolean {
  if (element.hidden) return false;
  if (element.getAttribute("aria-hidden") === "true") return false;
  if (typeof element.checkVisibility === "function" && !element.checkVisibility()) {
    return false;
  }
  return true;
}

function getFocusableChildren(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    isActuallyFocusable,
  );
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

    // Move focus into the container — an [autofocus] / [data-autofocus]
    // child wins, otherwise the first focusable child. `data-autofocus`
    // is the explicit form: React consumes the `autoFocus` prop without
    // reflecting it as a DOM attribute, so querySelector can't see it.
    const container = containerRef.current;
    const focusable = getFocusableChildren(container);
    const autofocused = container.querySelector<HTMLElement>("[autofocus],[data-autofocus]");
    const initial = autofocused ?? focusable[0];
    if (initial) {
      initial.focus();
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
      const activeEl = document.activeElement;

      // Focus on the container itself, or escaped outside it entirely
      // (programmatic focus elsewhere): pull it back in instead of
      // letting the browser walk into the background page.
      if (activeEl === container || !container.contains(activeEl)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
        return;
      }

      if (e.shiftKey) {
        // Shift+Tab: if at first element, wrap to last
        if (activeEl === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        // Tab: if at last element, wrap to first
        if (activeEl === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    // Document-level capture: keeps working even when focus has
    // escaped the container, and fires before consumer handlers.
    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
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
