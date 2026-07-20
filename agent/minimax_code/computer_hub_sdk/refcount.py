"""Refcounted-binding helper for the connection's bound-session set (R135).

Fusion of grok-build's ``xai-computer-hub-sdk/src/refcount.rs``. The
:class:`RefCountedSet` tracks the per-key borrow count behind a plain
:class:`dict` so the connection substrate can ``register_session`` once
per session (not once per consumer) and ``unregister_session`` only when
the LAST consumer drops its borrow. Multiple
:class:`~minimax_code.computer_hub_sdk` ToolServer / ToolHarness
instances against the same ``(url, principal)`` share a single
connection and refcount their session bindings through this set.

Concurrency model
-----------------

The Rust original stores counts in a ``dashmap::DashMap`` -- a lock-free
sharded concurrent map whose selling point is that increments and
decrements never serialise on a single mutex. Python's asyncio is a
single-threaded cooperative runtime, and every method on
:class:`RefCountedSet` is synchronous (no ``await`` point). asyncio only
suspends a task at an ``await``; absent one, an ``increment`` /
``decrement`` call runs start-to-finish without any other task
interleaving. A plain :class:`dict` is therefore the faithful Python
equivalent of ``DashMap`` here: the lock-free concurrency guarantee
degenerates to "no ``await`` = atomic", which :class:`dict` satisfies
natively. (Under a preemptive threading model this would not hold; the
SDK's connection layer is asyncio-only, so the dict is correct.)

Saturation
----------

Counts saturate at :data:`U64_MAX` (mirroring Rust ``u64`` +
``saturating_add`` / ``saturating_sub``). Python ``int`` is arbitrary
precision and never overflows, so the *overflow-prevention* motive for
saturation is absent; the *observable contract* is preserved anyway --
the count pins at :data:`U64_MAX` rather than growing past it, because
callers may rely on the monotonic no-rollover behaviour and the wire
representation of a borrow count is a ``u64``.

Module visibility
-----------------

The crate's ``lib.rs`` declares ``pub mod refcount`` (line 42) but does
**not** ``pub use refcount::RefCountedSet`` in its barrel (lines 48-71).
This landing matches that: :class:`RefCountedSet` is not re-exported
from the package barrel; callers reach it as
``minimax_code.computer_hub_sdk.refcount.RefCountedSet``.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Generic, TypeVar

__all__ = ["RefCountedSet", "U64_MAX"]

#: Inclusive ceiling for a borrow count, mirroring Rust ``u64::MAX``.
#: :meth:`RefCountedSet.increment` pins at this value; ``decrement`` never
#: rolls a count under zero. The wire representation of a borrow count is a
#: ``u64``, so growing past this would be a wire-level corruption.
U64_MAX: int = 2**64 - 1

K = TypeVar("K", bound=Hashable)


class RefCountedSet(Generic[K]):
    """Refcounted set keyed by ``K`` (R135).

    Each :meth:`increment` returns the new count; the corresponding
    :meth:`decrement` returns the count AFTER the decrement, so callers
    fire teardown when the result is ``0`` (the entry was removed) and
    treat ``None`` as an idempotent drop of an unknown key.

    Keys must be hashable (:class:`collections.abc.Hashable`). Mutating a
    key's identity after insertion is caller misuse and is not detected;
    use immutable identifiers (strings, tuples of primitives) as keys.
    """

    __slots__ = ("_counts",)

    def __init__(self) -> None:
        self._counts: dict[K, int] = {}

    def increment(self, key: K) -> tuple[int, int]:
        """Increment ``key``'s refcount, returning ``(prev, new)`` (R135).

        The ``0 -> 1`` edge (``prev == 0``) is the moment the
        protocol-level ``register_session`` call must fire; callers
        detect it off the returned ``prev``. The count saturates at
        :data:`U64_MAX` so a runaway caller cannot roll the count past
        the wire's ``u64`` ceiling.
        """
        prev = self._counts.get(key, 0)
        new = prev + 1 if prev < U64_MAX else U64_MAX
        self._counts[key] = new
        return prev, new

    def decrement(self, key: K) -> int | None:
        """Decrement ``key``'s refcount, returning the post-decrement count (R135).

        ``0`` means the entry was removed and the caller should fire the
        protocol-level ``unregister_session``. ``None`` means the key was
        not present -- an idempotent drop (e.g. a duplicate teardown) --
        and the caller should do nothing.
        """
        prev = self._counts.get(key)
        if prev is None:
            return None
        new = prev - 1 if prev > 0 else 0
        if new == 0:
            del self._counts[key]
        else:
            self._counts[key] = new
        return new

    def snapshot_keys(self) -> list[K]:
        """Snapshot the live keys into a fresh list (R135).

        Only used by the reconnect-replay path, which fires once per
        disconnect, so the allocation cost is negligible.
        """
        return list(self._counts.keys())

    def is_empty(self) -> bool:
        """``True`` when no key has a non-zero refcount."""
        return not self._counts

    def __len__(self) -> int:
        """Number of distinct live keys."""
        return len(self._counts)

    def __repr__(self) -> str:
        # Keys are session identifiers (potentially sensitive); mirror the
        # outbound-sanitisation precedent (R15) and surface only the
        # cardinality, never the keys themselves.
        return f"RefCountedSet(live_keys={len(self._counts)})"
