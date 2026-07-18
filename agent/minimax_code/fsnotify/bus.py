"""Single causal stream of file-change events (R16).

Python analogue of grok-build ``xai-fsnotify``'s broadcast channel. The
source is our own tool calls rather than an OS watcher, so the stream is
precise (no debounce noise, no lock state machine). Fail-open by design —
mirror of :class:`minimax_code.telemetry.engine.TelemetryEngine`: ``emit``
never raises, a slow subscriber never blocks the writer, and a bus
misconfiguration never breaks the agent.

Lifecycle: built lazily by :func:`minimax_code.app.ensure_fs_bus`; the
write tools call ``ensure_fs_bus()`` after a successful write and treat
``None`` as "disabled, zero overhead".
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque

from .events import FsEvent, FsEventKind

logger = logging.getLogger(__name__)


class FsEventBus:
    """Fail-open in-memory file-change bus.

    Parameters
    ----------
    capacity:
        Max events retained for ``recent()`` introspection. Default 500.
    subscriber_maxsize:
        Per-subscriber queue depth. A subscriber that falls further behind
        than this has events *dropped* (not blocking the writer) — the
        fail-open contract.
    """

    def __init__(
        self,
        *,
        capacity: int = 500,
        subscriber_maxsize: int = 256,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if subscriber_maxsize < 1:
            raise ValueError("subscriber_maxsize must be >= 1")
        self._recent: deque[FsEvent] = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue[FsEvent]] = []
        self._subscriber_maxsize = subscriber_maxsize
        self._seq = itertools.count(1)

    # ------------------------------------------------------------------
    # Emit (the hot path — never raises)
    # ------------------------------------------------------------------

    def emit(
        self,
        kind: FsEventKind | str,
        paths,
        cause: str,
        *,
        session_id: str | None = None,
        tool_call_id: str | None = None,
        **attributes,
    ) -> FsEvent | None:
        """Build + publish one event. Returns the event, or ``None`` on failure.

        Call sites treat this as fire-and-forget: a bus failure must never
        break the tool that just wrote the file. ``kind`` accepts the enum
        or its string value for ergonomics.
        """
        try:
            resolved = FsEventKind(kind) if not isinstance(kind, FsEventKind) else kind
            event = FsEvent(
                kind=resolved,
                paths=tuple(str(p) for p in paths),
                cause=str(cause),
                seq=next(self._seq),
                monotonic_ns=time.monotonic_ns(),
                session_id=session_id,
                tool_call_id=tool_call_id,
                attributes=tuple(sorted((str(k), str(v)) for k, v in attributes.items())),
            )
            self._recent.append(event)
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.debug("fs bus subscriber full; dropping event seq=%d", event.seq)
                except Exception:  # noqa: BLE001 — broken subscriber must not break others
                    logger.debug("fs bus subscriber publish failed", exc_info=True)
            return event
        except Exception:  # noqa: BLE001 — bus must never break callers
            logger.debug("fs bus emit failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Subscribe (async consumers)
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[FsEvent]:
        """Return a fresh bounded queue that will receive future events.

        Existing events are *not* replayed — subscribe is point-in-time.
        Unsubscribe via :meth:`unsubscribe` to avoid leaking queues.
        """
        q: asyncio.Queue[FsEvent] = asyncio.Queue(maxsize=self._subscriber_maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[FsEvent]) -> None:
        """Stop delivering to ``q``. No-op if never subscribed."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Read (introspection — best-effort, never raises)
    # ------------------------------------------------------------------

    def recent(
        self,
        *,
        limit: int = 100,
        kind: FsEventKind | str | None = None,
        cause: str | None = None,
        session_id: str | None = None,
    ) -> list[FsEvent]:
        """Return up to ``limit`` most-recent events, oldest-first.

        Optional ``kind`` / ``cause`` / ``session_id`` filters narrow the
        slice without mutating the buffer.
        """
        try:
            want_kind = FsEventKind(kind) if isinstance(kind, str) else kind
            out: list[FsEvent] = []
            for e in list(self._recent):
                if want_kind is not None and e.kind != want_kind:
                    continue
                if cause is not None and e.cause != cause:
                    continue
                if session_id is not None and e.session_id != session_id:
                    continue
                out.append(e)
            if limit and limit > 0:
                return out[-limit:]
            return out
        except Exception:  # noqa: BLE001 — read path is best-effort
            return []

    @property
    def buffered_count(self) -> int:
        return len(self._recent)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def clear(self) -> None:
        """Drop buffered events + detach all subscribers (mainly for tests)."""
        self._recent.clear()
        self._subscribers.clear()


__all__ = ["FsEventBus"]
