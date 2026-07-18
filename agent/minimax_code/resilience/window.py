"""Bounded sliding window over ``(timestamp, is_failure)`` samples (R17).

Port of grok-build ``xai-circuit-breaker``'s ``window.rs``. A ``deque`` of
``(monotonic_seconds, is_failure)`` pairs with an incremental failure count
so :meth:`error_rate` is O(1) — the breaker reads it on every ``record()``
and we never want a hot-path scan.

The ``MAX_WINDOW_ENTRIES`` safety cap bounds memory under sustained high
load (10K req/s × 60s window would otherwise reach 600K entries); once the
cap is reached the oldest sample is evicted regardless of age.
"""

from __future__ import annotations

from collections import deque

# Safety cap on sliding window entries to bound memory under sustained high
# load (e.g. 10K req/s * 60s window would otherwise reach 600K entries).
MAX_WINDOW_ENTRIES = 10_000


class SlidingWindow:
    """Bounded sliding window of ``(timestamp, is_failure)`` samples.

    ``timestamp`` is seconds on a monotonic clock (the breaker's own clock,
    not wall-clock). The incremental ``_failures`` counter is maintained on
    push/evict so ``error_rate()`` stays O(1).
    """

    __slots__ = ("_entries", "_failures")

    def __init__(self) -> None:
        self._entries: deque[tuple[float, bool]] = deque()
        self._failures = 0

    def push(self, is_failure: bool, at: float) -> None:
        """Append one sample at monotonic time ``at``.

        If the window is at capacity, the oldest sample is evicted first so
        the count never exceeds ``MAX_WINDOW_ENTRIES``.
        """
        if len(self._entries) >= MAX_WINDOW_ENTRIES:
            _, was_failure = self._entries.popleft()
            if was_failure:
                self._failures -= 1
        self._entries.append((at, is_failure))
        if is_failure:
            self._failures += 1

    def evict(self, window: float, now: float) -> None:
        """Drop samples older than ``window`` seconds measured back from ``now``."""
        cutoff = now - window
        entries = self._entries
        while entries and entries[0][0] < cutoff:
            _, was_failure = entries.popleft()
            if was_failure:
                self._failures -= 1

    def error_rate(self) -> float:
        """Failure fraction over the live window (``0.0`` when empty)."""
        if not self._entries:
            return 0.0
        return self._failures / len(self._entries)

    def sample_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._failures = 0


__all__ = ["MAX_WINDOW_ENTRIES", "SlidingWindow"]
