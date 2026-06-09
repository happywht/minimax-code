/**
 * Reusable hook for "click outside to close" behaviour.
 *
 * Attaches a `mousedown` listener on `document` and fires `onClose`
 * when the click target falls outside the given `ref` element.
 * Optionally also listens for the Escape key.
 *
 * Used by ModelSelector, GitStatusBar, WorkspaceSwitcher,
 * NotificationBell, and any future dropdown/popover components.
 */
import { useEffect } from "react";

export function useClickOutside(
  ref: React.RefObject<HTMLElement | null>,
  onClose: () => void,
  options: { enabled?: boolean; escape?: boolean } = {},
): void {
  const { enabled = true, escape = true } = options;

  useEffect(() => {
    if (!enabled) return;

    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };

    document.addEventListener("mousedown", onDown);
    if (escape) document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("mousedown", onDown);
      if (escape) document.removeEventListener("keydown", onKeyDown);
    };
  }, [ref, onClose, enabled, escape]);
}
