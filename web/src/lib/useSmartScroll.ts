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
  const pointerActiveRef = useRef(false);
  const resumeIntentRef = useRef(false);
  const previousKeyRef = useRef<unknown>(contentKey);
  const [isFollowing, setIsFollowing] = useState(true);
  const [newContentCount, setNewContentCount] = useState(0);

  const pauseFollowing = useCallback(() => {
    resumeIntentRef.current = false;
    followingRef.current = false;
    setIsFollowing(false);
  }, []);

  const updateFollowState = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Virtual row measurements and scroll anchoring also dispatch scroll.
    // Leaving the bottom disables follow only during an actual user gesture.
    // A queued scroll event can still report the old bottom after an upward
    // wheel gesture. Only new downward input (or a scrollbar drag) may resume.
    const nearBottom = distance <= thresholdPx;
    const next = followingRef.current
      ? nearBottom || !pointerActiveRef.current
      : nearBottom && (resumeIntentRef.current || pointerActiveRef.current);
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
    resumeIntentRef.current = false;
    setIsFollowing(true);
    setNewContentCount(0);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      updateFollowState();
    };
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) pauseFollowing();
      else if (event.deltaY > 0) resumeIntentRef.current = true;
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
        pauseFollowing();
      } else if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
        resumeIntentRef.current = true;
      }
    };
    const onPointerDown = () => { pointerActiveRef.current = true; };
    const onPointerUp = () => { pointerActiveRef.current = false; };
    let touchY: number | null = null;
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? null; };
    const onTouchMove = (event: TouchEvent) => {
      const nextY = event.touches[0]?.clientY;
      if (touchY != null && nextY != null && nextY > touchY) pauseFollowing();
      else if (touchY != null && nextY != null && nextY < touchY) resumeIntentRef.current = true;
      touchY = nextY ?? null;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("keydown", onKeyDown);
    el.addEventListener("pointerdown", onPointerDown, { passive: true });
    window.addEventListener("pointerup", onPointerUp, { passive: true });
    window.addEventListener("pointercancel", onPointerUp, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    updateFollowState();
    return () => {
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("keydown", onKeyDown);
      el.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
    };
  }, [pauseFollowing, updateFollowState]);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let resizeFrame: number | null = null;
    const observer = new ResizeObserver(() => {
      if (resizeFrame != null) cancelFrame(resizeFrame);
      resizeFrame = requestFrame(() => {
        resizeFrame = null;
        if (followingRef.current) scrollToBottom("auto");
      });
    });
    observer.observe(el);
    // The virtualizer's height changes after lazy rows and markdown measure.
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => {
      observer.disconnect();
      if (resizeFrame != null) cancelFrame(resizeFrame);
    };
  }, [contentKey, scrollToBottom]);

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
