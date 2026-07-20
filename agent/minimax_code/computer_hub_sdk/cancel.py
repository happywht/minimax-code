"""Per-session strict-cancellation registry (R141).

Fusion of grok-build's ``xai-computer-hub-sdk/src/cancel.rs`` (301 lines).
This is the SDK crate's 9th leaf (after R133 error / R134 handshake / R135
refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow /
R139 auth / R140 observability): maps each in-flight ``tool_call_id`` to its
cancellation :class:`asyncio.Event` so a ``Cancel`` hook (or session
teardown) can hard-cancel the running call by dropping its future. A small
``pending`` tombstone set covers the race where a ``Cancel`` arrives BEFORE
the dispatcher registered the token (the symmetric window to pre-spawn
registration): the id is tombstoned and the dispatcher cancels it at
registration time. One registry per session, tied to the session-loop
lifetime alongside the inbox and the per-session admission semaphore.

Near-total forward-port (this is a pure data-structure leaf). The Rust
concurrency primitives map cleanly to Python:

* ``DashMap<ToolCallId, CancellationToken>`` -> ``dict`` -- asyncio is
  single-threaded and the registry's critical sections contain no ``await``
  (the map/set mutations are synchronous), so a plain ``dict`` has the same
  observe-consistency the Rust shard locks provide.
* ``DashSet<ToolCallId>`` -> ``set``.
* ``AtomicBool`` (``closed``) -> ``bool`` (GIL-protected; set-once in
  :meth:`CancelRegistry.cancel_all` then observed by
  :meth:`~CancelRegistry.register`).
* ``tokio_util::sync::CancellationToken`` -> :class:`asyncio.Event`
  (established by R138 ``connection_borrow``): ``token.cancel()`` ->
  ``Event.set``; ``token.is_cancelled()`` -> ``Event.is_set``. The
  ``cancelled().await`` half lives in the dispatcher's ``execute_call`` task
  (``await Event.wait``), NOT here -- the registry only sets the fence,
  never awaits it.

YAGNI boundary
--------------

:meth:`CancelRegistry.register` keeps its Rust double-check of ``closed``
AFTER the insert even though Python's single-threaded asyncio cannot
interleave a ``cancel_all`` inside the synchronous register body. It is
retained for semantic fidelity with the Rust teardown-race fix and as
forward-compatible defense if the registry ever crosses a thread boundary;
the cost is one extra ``bool`` read.
"""

from __future__ import annotations

import asyncio

from minimax_code.tool_protocol.ids import ToolCallId

__all__ = ["CancelRegistry", "MAX_PENDING_TOMBSTONES"]

#: Upper bound on outstanding pre-registration tombstones. Tombstones cover
#: the microscopic window between a ``Cancel`` hook and the matching
#: ``register``, so in steady state the set holds a handful of entries. A
#: ``Cancel`` whose call never registers (e.g. one racing call completion,
#: after ``deregister`` already removed the live token) leaves a tombstone
#: that no ``register`` ever consumes; this cap reclaims such stragglers so
#: a single long-lived session cannot grow ``pending`` without bound.
MAX_PENDING_TOMBSTONES: int = 8192


class CancelRegistry:
    """Per-session ``tool_call_id -> asyncio.Event`` map + tombstone set (R141).

    Wraps a live-token map (``tool_call_id`` -> the call's cancellation
    :class:`asyncio.Event`), a pending-tombstone set for cancels that land
    before registration, and a set-once ``closed`` flag. Mirrors Rust
    ``pub(crate) struct CancelRegistry``.
    """

    def __init__(self) -> None:
        self._map: dict[ToolCallId, asyncio.Event] = {}
        self._pending: set[ToolCallId] = set()
        # Set once by cancel_all (teardown). After this, every new register
        # starts cancelled so a request dispatched in the teardown window
        # cannot escape as an orphaned, uncancellable task.
        self._closed: bool = False

    def register(self, call_id: ToolCallId, token: asyncio.Event) -> bool:
        """Register ``token`` for ``call_id`` before the call is spawned (R141).

        If a ``Cancel`` already tombstoned this id, the token is cancelled
        immediately so the call starts cancelled. Returns whether the token
        was pre-cancelled (by a tombstone or because the registry was torn
        down). Mirrors the Rust double-check of ``closed`` after the insert
        to close the teardown race (see the module YAGNI boundary note).
        """
        if self._closed:
            token.set()
            return True
        pre_cancelled = call_id in self._pending
        if pre_cancelled:
            self._pending.discard(call_id)
            token.set()
        self._map[call_id] = token
        # Re-check after the insert: semantic fidelity with the Rust
        # teardown-race fix (if cancel_all drained the map between our
        # closed-check and the insert, our entry would be missed). asyncio
        # is single-threaded so the race cannot fire today, but the check is
        # cheap forward-compatible defense if the registry ever crosses a
        # thread boundary.
        if self._closed:
            missed = self._map.pop(call_id, None)
            if missed is not None:
                missed.set()
            return True
        return pre_cancelled

    def cancel(self, call_id: ToolCallId) -> bool:
        """Cancel a live call, else tombstone the id (R141).

        Returns ``True`` when a live token was found and cancelled; ``False``
        when the id was tombstoned for the dispatcher to cancel at
        registration time. The tombstone set is capped at
        :data:`MAX_PENDING_TOMBSTONES`: a spurious cancel whose call never
        registers evicts one stale straggler before inserting so the set
        stays bounded.
        """
        token = self._map.pop(call_id, None)
        if token is not None:
            token.set()
            return True
        if len(self._pending) >= MAX_PENDING_TOMBSTONES:
            # Evict one straggler tombstone (a cancel whose call never
            # registered) before inserting so the set stays bounded. Mirrors
            # the Rust "collect key, then remove" idiom (here a single
            # iter/discard, since Python sets are not sharded).
            stale = next(iter(self._pending), None)
            if stale is not None:
                self._pending.discard(stale)
        self._pending.add(call_id)
        return False

    def deregister(self, call_id: ToolCallId) -> None:
        """Deregister a call's token on completion or cancel. Idempotent (R141)."""
        self._map.pop(call_id, None)

    def is_closed(self) -> bool:
        """Whether :meth:`cancel_all` has closed this registry (R141).

        A closed registry marks a session whose loop is (or is about to be)
        torn down -- used by the soft-rebind liveness gate.
        """
        return self._closed

    def cancel_all(self) -> int:
        """Drain-and-cancel every live token and close the registry (R141).

        Used on session teardown (``unbind_session`` / ``shutdown`` / full
        rebind of a dead loop) so detached ``execute_call`` tasks wind down
        promptly AND any call dispatched in the teardown window starts
        cancelled (see :meth:`register`). Returns the number of tokens
        cancelled. Idempotent: a second call cancels nothing.
        """
        # Mark closed BEFORE draining so a concurrent register either
        # observes the close (and self-cancels) or has its entry drained
        # here -- never both-miss.
        self._closed = True
        cancelled = 0
        for token in self._map.values():
            token.set()
            cancelled += 1
        self._map.clear()
        # Drop tombstones too: teardown closes the registry, so no future
        # register will consume them. Leaving them would let a stale
        # straggler set survive to the end of the (already-done) session.
        self._pending.clear()
        return cancelled

    def live_count(self) -> int:
        """Number of live (registered, not-yet-cancelled) tokens (test accessor)."""
        return len(self._map)

    def pending_count(self) -> int:
        """Number of outstanding pre-registration tombstones (test accessor)."""
        return len(self._pending)
