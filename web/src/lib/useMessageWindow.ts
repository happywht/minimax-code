/**
 * Hook for windowed message rendering.
 *
 * Instead of rendering all messages at once (which can create hundreds
 * of DOM nodes in long conversations), this hook maintains a sliding
 * window from the end of the array — showing only the most recent
 * `windowSize` messages by default.
 *
 * The user can click "load earlier" to expand the window by
 * `windowSize` messages at a time.
 *
 * @param messages  Full message array (newest last)
 * @param windowSize  How many messages per window (default 50)
 * @returns visible messages, hasMore flag, and loadMore callback
 */
import { useCallback, useMemo, useState } from "react";

export interface MessageWindowState<T> {
  /** Messages currently visible (subset of the full list) */
  visible: T[];
  /** Total messages in the full list */
  total: number;
  /** Whether there are older messages not yet shown */
  hasMore: boolean;
  /** How many messages are hidden (at the top) */
  hiddenCount: number;
  /** Expand the window by one page of older messages */
  loadMore: () => void;
}

export function useMessageWindow<T>(
  messages: T[],
  windowSize: number = 50,
): MessageWindowState<T> {
  const [extraPages, setExtraPages] = useState(0);
  const limit = windowSize * (extraPages + 1);

  // When new messages arrive, reset to showing the latest window
  // unless the user has explicitly loaded more.
  const total = messages.length;
  const startIndex = Math.max(0, total - limit);
  const visible = useMemo(
    () => messages.slice(startIndex),
    [messages, startIndex],
  );
  const hasMore = startIndex > 0;
  const hiddenCount = startIndex;

  const loadMore = useCallback(() => {
    setExtraPages((p) => p + 1);
  }, []);

  return { visible, total, hasMore, hiddenCount, loadMore };
}
