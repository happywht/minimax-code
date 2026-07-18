"""Bounded ring buffer for recent telemetry events (R11).

In-process, lock-protected, fixed-capacity FIFO. The engine appends every
redacted event here so ``telemetry.recent`` can return the last N moments
without a DB round-trip — the same role grok-build's ``memory_log.rs``
plays for its unified log.

Thread-safe via a single :class:`threading.Lock`; the hot path (``append``)
is one ``deque`` write under the lock, so contention is negligible even
when tools fire events rapidly.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any


class RingBuffer:
    """Fixed-capacity, thread-safe ring buffer."""

    def __init__(self, capacity: int = 500) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._buf: deque[Any] = deque(maxlen=capacity)
        self._lock = Lock()
        self._capacity = capacity

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(self, item: Any) -> None:
        """Append one item; oldest item is dropped when at capacity."""
        with self._lock:
            self._buf.append(item)

    def recent(self, limit: int = 100) -> list[Any]:
        """Return up to ``limit`` most-recent items in insertion order."""
        if limit < 1:
            return []
        with self._lock:
            snapshot = list(self._buf)
        if len(snapshot) <= limit:
            return snapshot
        return snapshot[-limit:]

    def clear(self) -> None:
        """Drop all buffered items (mainly for tests)."""
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


__all__ = ["RingBuffer"]
