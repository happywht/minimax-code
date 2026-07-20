"""Connection-borrow lifecycle: at-most-once teardown guard + shutdown fence (R138).

Fusion of grok-build's ``xai-computer-hub-sdk/src/connection_borrow.rs`` (224
lines). This is the crate-private lifecycle leaf shared by ``ToolServer`` and
``ToolHarness`` (both later leaves): it wraps a pooled ``HubConnection`` with a
shutdown fence and an at-most-once ``torn_down`` guard so the teardown sequence
runs exactly once across explicit ``shutdown`` and the ``Drop`` fallback.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

The Rust original is half pure-lifecycle, half pool/auth/connection framework
glue. ``HubConnectionPool`` (``pool.rs``, 344 lines), ``HubConnection``
(``connection.rs``, 2694 lines) and ``AuthProvider`` (``auth.rs``, 238 lines)
are all later leaves, so the ``acquire`` factory (which resolves a pool entry
and refcount-binds sessions) is declared out of scope here rather than ported
into dead code.

MIGRATED (transport-agnostic pure lifecycle):

* :class:`ConnectionBorrow` -- the borrow handle: an opaque connection
  reference + a shutdown fence (:class:`asyncio.Event`, the Python analogue of
  ``tokio_util::sync::CancellationToken``) + an at-most-once ``torn_down``
  guard (:class:`threading.Lock`-protected CAS).
* :meth:`ConnectionBorrow.begin_teardown` -- at-most-once transition; returns
  ``True`` for the single winner. Mirrors ``compare_exchange(false, true,
  SeqCst, SeqCst).is_ok()``.
* :meth:`ConnectionBorrow.shutdown_token` -- returns the shutdown
  :class:`asyncio.Event`. ``cancel`` -> ``Event.set``;
  ``cancelled().await`` -> ``await Event.wait``.
* :meth:`ConnectionBorrow.request_shutdown` -- convenience: win
  ``begin_teardown`` then ``set`` the fence (mirrors server.rs:1731 +
  harness.rs:1651 ``begin_teardown`` + ``cancel``).
* :meth:`ConnectionBorrow.from_connection` -- factory that bypasses the pool
  (the pool-dependent ``acquire`` is out of scope). Lets a future leaf
  construct a borrow from an already-resolved connection.

NOT MIGRATED (framework glue with no Python equivalent yet):

* ``acquire`` factory -- depends on ``HubConnectionPool::get_or_connect_tuned``
  (pool.rs) + ``AuthProvider`` (auth.rs) + ``HubConnection`` (connection.rs)
  + ``ConnectCallback`` / ``ConnectionTuning`` / ``DisconnectCallback`` /
  ``ReconnectCallback``. All later leaves. Its GEOMETRY is: build a pool entry
  then wrap it; :meth:`from_connection` is the pool-free half.
* ``tokio_util::sync::CancellationToken`` -- no Python equivalent in the
  asyncio stdlib; :class:`asyncio.Event` is the close analogue (single-set,
  awaitable). ``clone`` is a no-op in Python (Event is shared by reference).
* ``connection() -> &Arc<HubConnection>`` accessor returning a typed handle --
  the :attr:`connection` property returns ``object`` until ``connection.rs``
  lands; callers that need typed methods wait for that leaf.
* ``Debug`` impl -- :meth:`__repr__` exposes ``torn_down`` (non-exhaustive,
  like ``finish_non_exhaustive``).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ConnectionBorrow"]


@dataclass
class ConnectionBorrow:
    """Borrowed slice of a pooled connection + at-most-once teardown guard (R138).

    Wraps an opaque connection handle (``HubConnection`` once ``connection.rs``
    lands) with a shutdown fence and an at-most-once ``torn_down`` guard so the
    teardown sequence is at-most-once across explicit shutdown and the
    fallback. Mirrors Rust ``pub(crate) struct ConnectionBorrow``.

    The borrow is constructed via :meth:`from_connection` (pool-free); the
    pool-dependent ``acquire`` factory is out of scope (YAGNI boundary in the
    module docstring).
    """

    connection: Any
    _shutdown: asyncio.Event = field(default_factory=asyncio.Event)
    _torn_down: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_connection(cls, connection: Any) -> ConnectionBorrow:
        """Construct a borrow from an already-resolved connection (R138).

        Pool-free half of the Rust ``acquire`` factory: the pool / auth /
        callback resolution that ``acquire`` performs is declared out of scope
        (pool.rs / auth.rs / connection.rs are later leaves). A future leaf
        that owns pool resolution calls this with the resolved handle.
        """
        return cls(connection=connection)

    @property
    def is_torn_down(self) -> bool:
        """Whether teardown has begun (observable, mirrors the Debug field)."""
        return self._torn_down

    def begin_teardown(self) -> bool:
        """At-most-once teardown transition; ``True`` for the single winner (R138).

        Mirrors ``torn_down.compare_exchange(false, true, SeqCst,
        SeqCst).is_ok()``: the first caller wins, every later caller observes
        the already-torn-down state. The :class:`threading.Lock` makes the CAS
        safe even under real threading (Rust's ``AtomicBool`` is lock-free;
        Python's GIL + asyncio single-thread makes a plain bool sufficient in
        practice, but the lock future-proofs the guarantee for thread callers).
        """
        with self._lock:
            if self._torn_down:
                return False
            self._torn_down = True
            return True

    def shutdown_token(self) -> asyncio.Event:
        """Return the shutdown fence (Python analogue of ``CancellationToken``).

        ``token.cancel()`` -> ``Event.set``; ``token.cancelled().await`` ->
        ``await Event.wait``. The token is shared by reference (Rust
        ``clone`` is a no-op here: Python passes the same ``Event`` object).
        """
        return self._shutdown

    def request_shutdown(self) -> bool:
        """Win ``begin_teardown`` then set the shutdown fence (R138).

        Mirrors the server.rs:1731 + harness.rs:1651 idiom:
        ``if borrow.begin_teardown() { borrow.shutdown_token().cancel() }``.
        Returns whether THIS caller won the at-most-once transition (and thus
        performed the cancel); ``False`` means teardown already began.
        """
        if not self.begin_teardown():
            return False
        self._shutdown.set()
        return True

    def __repr__(self) -> str:
        return f"ConnectionBorrow(torn_down={self._torn_down})"
