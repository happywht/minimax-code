/**
 * Shared clipboard hook — copies text and exposes a transient
 * `copied` flag that auto-resets. Used by code blocks, mermaid
 * blocks, file-reference cards, and the message-level copy action.
 *
 * `copy()` resolves to `true` on success and `false` when the
 * clipboard is unavailable, so callers can surface their own
 * feedback (e.g. a toast) on failure.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface CopyFeedback {
  copied: boolean;
  copy: (text: string) => Promise<boolean>;
}

export function useCopyFeedback(resetMs = 1500): CopyFeedback {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear the pending reset timer on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        return false;
      }
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), resetMs);
      return true;
    },
    [resetMs],
  );

  return { copied, copy };
}
