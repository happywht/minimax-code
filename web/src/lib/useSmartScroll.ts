import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

export interface UseSmartScrollOptions {
  contentKey: unknown;
  thresholdPx?: number;
}

export interface SmartScrollState<T extends HTMLElement> {
  containerRef: RefObject<T>;
  isFollowing: boolean;
  showNewContentButton: boolean;
  newContentCount: number;
  scrollToBottom: (behavior?: ScrollBehavior) => void;
}

export function useSmartScroll<T extends HTMLElement = HTMLDivElement>({
  contentKey,
  thresholdPx = 50,
}: UseSmartScrollOptions): SmartScrollState<T> {
  const containerRef = useRef<T>(null);
  const followingRef = useRef(true);
  const frameRef = useRef<number | null>(null);
  const previousKeyRef = useRef<unknown>(contentKey);
  const [isFollowing, setIsFollowing] = useState(true);
  const [newContentCount, setNewContentCount] = useState(0);

  const updateFollowState = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const next = distance <= thresholdPx;
    followingRef.current = next;
    setIsFollowing(next);
    if (next) setNewContentCount(0);
    return next;
  }, [thresholdPx]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const el = containerRef.current;
    if (!el) return;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior });
    } else {
      el.scrollTop = el.scrollHeight;
    }
    followingRef.current = true;
    setIsFollowing(true);
    setNewContentCount(0);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      updateFollowState();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    updateFollowState();
    return () => {
      el.removeEventListener("scroll", onScroll);
    };
  }, [updateFollowState]);

  useLayoutEffect(() => {
    if (Object.is(previousKeyRef.current, contentKey)) return;
    previousKeyRef.current = contentKey;

    if (frameRef.current != null) cancelFrame(frameRef.current);
    frameRef.current = requestFrame(() => {
      if (followingRef.current) {
        scrollToBottom("auto");
        frameRef.current = requestFrame(() => {
          frameRef.current = null;
          if (followingRef.current) scrollToBottom("auto");
        });
      } else {
        frameRef.current = null;
        setNewContentCount((count) => count + 1);
      }
    });
  }, [contentKey, scrollToBottom]);

  useEffect(() => {
    return () => {
      if (frameRef.current != null) cancelFrame(frameRef.current);
    };
  }, []);

  return {
    containerRef,
    isFollowing,
    showNewContentButton: !isFollowing || newContentCount > 0,
    newContentCount,
    scrollToBottom,
  };
}

function requestFrame(cb: FrameRequestCallback): number {
  if (typeof requestAnimationFrame === "function") return requestAnimationFrame(cb);
  return window.setTimeout(() => cb(performance.now()), 0);
}

function cancelFrame(id: number): void {
  if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(id);
  else clearTimeout(id);
}
