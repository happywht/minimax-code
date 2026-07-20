"""Process-wide connection pool keyed by ``(url, principal)`` (R143).

Fusion of grok-build's ``xai-computer-hub-sdk/src/pool.rs`` (344 lines). This
is the SDK crate's 11th leaf (after R133 error / R134 handshake / R135 refcount
/ R136 donate_pump / R137 trace_donate / R138 connection_borrow / R139 auth /
R140 observability / R141 cancel / R142 admission): the canonical entry point
for opening a server connection. Two ``ToolServer`` builds with the same
``(url, credential)`` observe the same pooled connection; distinct credentials
open distinct sockets. Direct ``HubConnection::connect`` calls are reserved
for tests and one-shot programs that explicitly want unpooled behaviour.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

MIGRATED (transport-agnostic pool bookkeeping):

* Two default constants (:data:`DEFAULT_POOL_IDLE_TTL` /
  :data:`DEFAULT_POOL_SWEEP_INTERVAL`).
* :class:`ConnKey` -- the ``(url, principal)`` pool key. Rust defines this in
  ``connection.rs`` (a later leaf); it is so small and so central to the pool's
  dedup identity that it migrates here as a frozen dataclass keyed on R139's
  :class:`~minimax_code.computer_hub_sdk.auth.PrincipalKey`.
* :class:`HubConnectionPool` -- the registry: :meth:`get_or_connect`
  (dedup-on-hit / open-on-miss / race-resolve) / :meth:`forget` /
  :meth:`forget_if` (ABA-safe) / :meth:`sweep_idle` (unused + idle eviction) /
  :meth:`spawn_idle_reaper` (background sweep).
* :class:`PooledConnection` -- the borrow handle an acquirer holds; its
  :meth:`~PooledConnection.release` returns the slot (the Python analogue of
  Rust's ``Arc<HubConnection>`` drop).
* :func:`host_is_loopback` -- the small loopback predicate Rust lives in
  ``connection.rs``; reproduced here so the insecure-scheme guard works before
  that leaf lands.
* :func:`shared` -- the process-global singleton with a reaper (mirrors Rust
  ``OnceCell``).

NOT MIGRATED (framework glue with no Python equivalent yet):

* ``HubConnection`` + ``HubConnection::connect`` + ``ConnectionConfig`` /
  ``ConnectionTuning`` / ``ConnectCallback`` / ``ReconnectCallback`` /
  ``DisconnectCallback`` -- the concrete connection actor, its open path, and
  its tuning/callback bundle all live in ``connection.rs`` (2694 lines, a later
  leaf). The pool depends on them only through:
  (1) :class:`HubConnection` -- modelled here as a :class:`typing.Protocol`
      with a single ``kind() -> ConnectionKind`` accessor (the only method the
      pool's kind-mismatch guard calls);
  (2) ``HubConnection::connect`` -- modelled as an injectable ``opener``
      callable (:data:`HubConnectionOpener`). The process singleton resolves
      its opener from the module-level :data:`_DEFAULT_OPENER` slot, which the
      ``connection.rs`` leaf will populate at import time. Tests pass a fake
      opener to :class:`HubConnectionPool` so the pool's bookkeeping is
      exercised without a real WebSocket.
* ``get_or_connect_tuned`` -- the Rust ``tuning`` knob binds at socket-open
  time and is a no-op on a pool hit; in Python the opener owns tuning, so the
  single :meth:`get_or_connect` entry point covers both shapes.
* ``crate::metrics::pool_connections_inc`` / ``..._dec`` /
  ``pool_evictions_inc`` -- the pool gauges live in ``metrics.rs`` (552 lines,
  a later leaf). They are replaced by no-op stubs whose signatures are
  preserved so the metrics leaf replaces the bodies without touching call
  sites (the same YAGNI pattern R140/R142 established).

Python-specific adaptations (no behaviour change):

* ``Arc<HubConnection>`` + ``Arc::strong_count == 1`` (the sweep ``unused``
  test) -> an explicit ``in_use`` borrow counter on :class:`_Pooled` plus a
  :class:`PooledConnection` handle whose :meth:`~PooledConnection.release`
  decrements it. Rust relies on RAII drop to return the slot; Python has no
  drop, so the handle is also an async context manager
  (``async with pool.get_or_connect(...) as conn:``) that releases on exit.
  The semantic invariant is identical: a connection with ``in_use == 0`` has
  no live consumer, exactly mirroring ``strong_count == 1`` (only the pool
  holds it).
* ``tokio::sync::OnceCell`` (async-aware lazy init) -> a module-level
  ``Optional`` singleton. asyncio is single-threaded, so the first-use
  read+init is atomic without a lock (mirrors R142 ``global_semaphore``).
* ``tokio::spawn`` + ``tokio::time::interval`` + ``Arc::downgrade`` (a reaper
  that never keeps the pool alive) -> :func:`asyncio.create_task` on a loop
  that holds a :class:`weakref.ref` to the pool; the task exits once the last
  strong reference drops (mirrors Rust's ``weak.upgrade()`` check).
* ``DashMap`` -> plain ``dict`` (asyncio is single-threaded; the pool's
  critical sections contain no ``await`` between the get-check and the insert,
  so the race Rust resolves with ``entry().or_insert_with`` is reproduced by a
  get-then-open-then-recheck sequence).
* ``DashMap::retain`` (mutate during iteration) -> collect evictable keys
  first, delete after (Python forbids mutating a dict during iteration).
* ``url::Url`` -> ``str`` + :func:`urllib.parse.urlparse` (scheme + host).
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from minimax_code.computer_hub_sdk.auth import AuthProvider, PrincipalKey
from minimax_code.computer_hub_sdk.error import InsecureScheme, InvalidConfig
from minimax_code.tool_protocol.connection import ConnectionKind

__all__ = [
    "DEFAULT_POOL_IDLE_TTL",
    "DEFAULT_POOL_SWEEP_INTERVAL",
    "ConnKey",
    "HubConnection",
    "HubConnectionOpener",
    "HubConnectionPool",
    "PooledConnection",
    "host_is_loopback",
    "shared",
]

#: Idle window for the reaper: a pooled connection is evictable once it is
#: unused (``in_use == 0``, i.e. only the pool holds it) **and**
#: ``now - last_handout >= DEFAULT_POOL_IDLE_TTL``. The clock is
#: ``last_handout`` (the last time the pool returned the connection), not the
#: moment the last consumer released it: a connection held longer than the TTL
#: and then released is eligible on the very next sweep, with no extra
#: post-release grace period. Tuned well above the server's own 90s dead-peer
#: idle timeout so a short borrow between turns of an active conversation is
#: not churned.
DEFAULT_POOL_IDLE_TTL: float = 300.0

#: How often the shared pool's idle reaper scans for evictable entries.
DEFAULT_POOL_SWEEP_INTERVAL: float = 60.0

#: Hostnames that qualify as loopback and are therefore exempt from the
#: ``wss://``-only rule. Mirrors the ``connection::host_is_loopback`` host set
#: (``127.0.0.1`` / ``::1`` / ``localhost``).
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _metrics_pool_connections_inc() -> None:
    """YAGNI boundary: ``crate::metrics::pool_connections_inc`` (R143).

    No-op until the ``metrics.rs`` leaf (552 lines) lands. Signature matches
    the Rust call so the metrics leaf replaces the body without touching the
    :meth:`HubConnectionPool.get_or_connect` call site.
    """
    return None


def _metrics_pool_connections_dec() -> None:
    """YAGNI boundary: ``crate::metrics::pool_connections_dec`` (R143)."""
    return None


def _metrics_pool_evictions_inc() -> None:
    """YAGNI boundary: ``crate::metrics::pool_evictions_inc`` (R143)."""
    return None


def host_is_loopback(url: str) -> bool:
    """Whether ``url`` points at a loopback host (R143).

    Reproduced here from ``connection::host_is_loopback`` (a ``connection.rs``
    helper not yet migrated) so the insecure-scheme guard works today. The
    ``connection.rs`` leaf may re-export or replace this; until then it is the
    single source for the pool's loopback test. A URL with no parseable host
    (e.g. a malformed string) is treated as non-loopback, so the caller fails
    closed into :class:`InsecureScheme` rather than silently allowing it.
    """
    host = urlparse(url).hostname
    if host is None:
        return False
    return host.lower() in _LOOPBACK_HOSTS


@runtime_checkable
class HubConnection(Protocol):
    """Minimal pooled-connection contract (R143 YAGNI boundary).

    The concrete ``HubConnection`` (``connection.rs``, 2694 lines) has not
    landed. The pool touches a connection only through :meth:`kind` (the
    kind-mismatch guard), so this Protocol is the full surface the pool needs.
    The ``connection.rs`` leaf's concrete class will satisfy this structurally;
    tests build a duck-typed stand-in (any object with a ``kind()`` method).
    """

    def kind(self) -> ConnectionKind:
        """Role this socket announced in its hello frame (R143)."""
        ...


@dataclass(frozen=True)
class ConnKey:
    """Pool dedup identity: ``(url, principal)`` (R143).

    Two builds whose URL and :class:`~minimax_code.computer_hub_sdk.auth.PrincipalKey`
    compare equal reuse one pooled connection; distinct credentials (or URLs)
    open distinct sockets. ``frozen=True`` derives ``__eq__`` / ``__hash__``
    over both fields, and :class:`PrincipalKey` is itself frozen + hashable, so
    a :class:`ConnKey` is usable as a ``dict`` key directly. Rust defines this
    in ``connection.rs``; it migrates with the pool because the pool owns the
    dedup identity.
    """

    url: str
    principal: PrincipalKey


#: Injectable stand-in for ``HubConnection::connect`` (R143 YAGNI boundary).
#:
#: NOTE: this is a runtime type alias (not an annotation), so every name it
#: references must already be bound at the point of assignment -- hence the
#: alias sits below :class:`ConnKey` (and the :class:`HubConnection` Protocol
#: above), unlike annotation-only forward references.
#:
#: The concrete open path lives in ``connection.rs``; until it lands, each
#: :class:`HubConnectionPool` opens new connections via this callable. The
#: process singleton (:func:`shared`) resolves its opener from the
#: module-level :data:`_DEFAULT_OPENER` slot (which the ``connection.rs`` leaf
#: populates at import time); tests inject a fake opener directly.
HubConnectionOpener = Callable[
    [ConnKey, AuthProvider, ConnectionKind], Awaitable[HubConnection]
]


@dataclass
class _Pooled:
    """A pooled connection plus bookkeeping for the reaper (R143 internal).

    ``last_handout`` is refreshed on every :meth:`get_or_connect` hit (and on
    the initial insert), so a connection that is repeatedly re-fetched never
    looks idle. ``in_use`` is the live consumer count: incremented when a
    :class:`PooledConnection` is handed out, decremented on
    :meth:`PooledConnection.release`. ``in_use == 0`` is the Python analogue
    of Rust's ``Arc::strong_count == 1`` (only the pool holds it), so the
    reaper's ``unused`` test is faithful without a real reference-counted
    pointer.
    """

    conn: HubConnection
    last_handout: float
    in_use: int = 0


class PooledConnection:
    """Borrow handle for a pooled connection (R143).

    Returned by :meth:`HubConnectionPool.get_or_connect`. The ``conn``
    attribute exposes the underlying :class:`HubConnection`; :meth:`release`
    returns the slot to the pool (the Python analogue of Rust's
    ``Arc<HubConnection>`` drop). Release is idempotent. The handle is also an
    async context manager so the idiomatic shape is::

        async with pool.get_or_connect(url, cred, kind) as conn:
            ...  # conn.kind() etc.

    The handle holds a :class:`weakref.ref` to the pool so a leaked handle
    does not keep an unshared test pool alive.
    """

    __slots__ = ("_conn", "_pool", "_key", "_released")

    def __init__(
        self,
        conn: HubConnection,
        pool: HubConnectionPool,
        key: ConnKey,
    ) -> None:
        self._conn = conn
        # weakref so a stray handle does not keep a test pool alive (mirrors
        # the reaper's Weak relationship to the pool).
        self._pool = weakref.ref(pool)
        self._key = key
        self._released = False

    @property
    def conn(self) -> HubConnection:
        """The underlying pooled connection (R143)."""
        return self._conn

    def release(self) -> None:
        """Return this slot to the pool (idempotent) (R143)."""
        if self._released:
            return
        self._released = True
        pool = self._pool()
        if pool is not None:
            pool._release(self._key)

    async def __aenter__(self) -> HubConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        self.release()

    def __repr__(self) -> str:
        return f"PooledConnection(key={self._key!r}, released={self._released})"


# Process-wide default opener slot. ``None`` until the ``connection.rs`` leaf
# populates it (it owns the real ``HubConnection::connect``). A pool whose
# opener is ``None`` AND that hits this unset default raises
# :class:`InvalidConfig` on the first open attempt -- the singleton reaper /
# bookkeeping APIs still work without a real opener, so the pool module can
# import and be unit-tested before ``connection.rs`` lands.
_DEFAULT_OPENER: HubConnectionOpener | None = None


def set_default_opener(opener: HubConnectionOpener | None) -> None:
    """Register the process-wide HubConnection opener (R143).

    Called by the ``connection.rs`` leaf once the real
    ``HubConnection::connect`` is available, so :func:`shared` and unpooled
    ``HubConnectionPool()`` instances open real sockets without each caller
    threading an opener through. Tests that need an opener pass it to the
    :class:`HubConnectionPool` constructor directly and never touch this slot.
    """
    global _DEFAULT_OPENER
    _DEFAULT_OPENER = opener


class HubConnectionPool:
    """Pool of live server connections keyed by ``(url, principal)`` (R143).

    Mirrors Rust ``pub struct HubConnectionPool``. The process singleton is
    obtained via :func:`shared` (which also starts the idle reaper); tests use
    :meth:`new` (no reaper, opener injected) for isolation.
    """

    def __init__(self, opener: HubConnectionOpener | None = None) -> None:
        self._connections: dict[ConnKey, _Pooled] = {}
        self._opener: HubConnectionOpener | None = opener

    def __repr__(self) -> None:
        # Mirrors Rust Debug: only the connection_count is surfaced (the
        # entries themselves carry secrets via PrincipalKey and are redacted).
        return f"HubConnectionPool(connection_count={len(self._connections)})"

    # -- construction -------------------------------------------------------

    @classmethod
    def new(cls, opener: HubConnectionOpener | None = None) -> HubConnectionPool:
        """Build a fresh, unshared pool (R143).

        Tests use this so each test sees an isolated registry (the process
        singleton would leak entries across tests). An ``opener`` is required
        for any test that exercises :meth:`get_or_connect`'s open path; tests
        that only probe ``sweep_idle`` / ``forget`` / ``forget_if`` may omit
        it. Unpooled pools do NOT start a reaper -- call
        :meth:`spawn_idle_reaper` explicitly, or use :func:`shared`.
        """
        return cls(opener=opener)

    # -- core acquire -------------------------------------------------------

    async def get_or_connect(
        self,
        url: str,
        credential: AuthProvider,
        kind: ConnectionKind,
        *,
        allow_insecure_ws: bool = False,
    ) -> PooledConnection:
        """Look up a pooled ``(url, principal)`` entry or open a fresh one (R143).

        On a hit the existing connection is returned (with ``last_handout``
        refreshed) after a kind-mismatch guard: mixing
        :class:`~minimax_code.tool_protocol.connection.ConnectionKind` values
        for the same ``(url, principal)`` is a caller error and surfaces as
        :class:`~minimax_code.computer_hub_sdk.error.InvalidConfig`. On a miss
        the opener is awaited, then a second get re-resolves the race window
        (another caller may have inserted between the first get and the open):
        the race-loser drops its fresh connection and adopts the winner.

        The plaintext-scheme guard is re-checked on every call so a cached
        insecure entry cannot bypass it.
        """
        # Plaintext-scheme guard: wss:// always allowed; ws:// only to a
        # loopback host or when the caller explicitly opts in.
        scheme = urlparse(url).scheme.lower()
        if scheme != "wss" and not host_is_loopback(url) and not allow_insecure_ws:
            raise InsecureScheme(url)

        key = ConnKey(url=url, principal=credential.principal_key())

        existing = self._connections.get(key)
        if existing is not None:
            existing.last_handout = time.monotonic()
            if existing.conn.kind() != kind:
                raise InvalidConfig(
                    f"pool entry for {key.url} bound to {existing.conn.kind()!r}; "
                    f"rebuild requested {kind!r}"
                )
            existing.in_use += 1
            _metrics_pool_connections_inc()  # gauge steady-state; no-op stub
            return PooledConnection(existing.conn, self, key)

        # Miss: open a fresh connection via the injected opener.
        conn = await self._open(key, credential, kind)

        # Race window: another caller may have inserted between our get and
        # the open. Re-resolve -- if we lost, drop our fresh connection and
        # adopt the winner (entry().or_insert_with semantics).
        winner = self._connections.get(key)
        if winner is not None:
            winner.last_handout = time.monotonic()
            if winner.conn.kind() != kind:
                raise InvalidConfig(
                    f"pool entry for {key.url} bound to {winner.conn.kind()!r}; "
                    f"rebuild requested {kind!r}"
                )
            winner.in_use += 1
            # Drop our fresh connection: it is not stored anywhere, so letting
            # `conn` go out of scope is the Python analogue of Rust's `drop(conn)`.
            del conn
            return PooledConnection(winner.conn, self, key)

        # Won the race (or no race): insert our fresh connection.
        _metrics_pool_connections_inc()
        self._connections[key] = _Pooled(
            conn=conn,
            last_handout=time.monotonic(),
            in_use=1,
        )
        return PooledConnection(conn, self, key)

    async def _open(
        self,
        key: ConnKey,
        credential: AuthProvider,
        kind: ConnectionKind,
    ) -> HubConnection:
        """Resolve the opener and open a new connection (R143 internal).

        The opener is the pool's own if set, else the module-level default
        (populated by the ``connection.rs`` leaf). If neither is set the pool
        cannot open a real connection and fails fast with :class:`InvalidConfig`
        -- this keeps the bookkeeping APIs usable before ``connection.rs``
        lands while making the open path's dependency explicit.
        """
        opener = self._opener or _DEFAULT_OPENER
        if opener is None:
            raise InvalidConfig(
                "no HubConnection opener registered "
                "(connection.rs not yet migrated); "
                "pass opener= to HubConnectionPool for tests"
            )
        return await opener(key, credential, kind)

    def _release(self, key: ConnKey) -> None:
        """Decrement the ``in_use`` borrow count for ``key`` (R143 internal).

        Called by :meth:`PooledConnection.release`. Idempotent in the sense
        that a release for an already-removed / unknown key is a no-op (the
        connection may have been swept or forgotten between hand-out and
        release); the floor is 0.
        """
        pooled = self._connections.get(key)
        if pooled is not None and pooled.in_use > 0:
            pooled.in_use -= 1

    # -- introspection ------------------------------------------------------

    def __len__(self) -> int:
        """Number of pooled connections (R143)."""
        return len(self._connections)

    def is_empty(self) -> bool:
        """``True`` when no connection is pooled (R143)."""
        return len(self._connections) == 0

    # -- eviction -----------------------------------------------------------

    def forget(self, key: ConnKey) -> None:
        """Forget the pooled connection for ``key`` (R143).

        The underlying connection object is dropped only when no other holder
        keeps a reference (a live :class:`PooledConnection` still sees it via
        its ``conn`` attribute); the next :meth:`get_or_connect` for the same
        key opens a fresh socket.
        """
        if self._connections.pop(key, None) is not None:
            _metrics_pool_connections_dec()

    def forget_if(
        self,
        key: ConnKey,
        predicate: Callable[[HubConnection], bool],
    ) -> bool:
        """Identity-checked forget: removes the slot only when ``predicate`` accepts (R143).

        The self-evicting connection actor passes an identity check
        (``conn is expected``) so a race-loser can never drop the winner's
        fresh entry (ABA-safe). Returns whether the entry was removed -- the
        Rust original returns ``()`` but reports the same fact internally via
        ``remove_if().is_some()``; the bool is surfaced here for testability.
        """
        pooled = self._connections.get(key)
        if pooled is not None and predicate(pooled.conn):
            del self._connections[key]
            _metrics_pool_connections_dec()
            return True
        return False

    def sweep_idle(self, idle_ttl: float) -> int:
        """Close and remove every pooled connection that is BOTH unused (R143).

        A connection is evictable when ``in_use == 0`` (no live consumer --
        only the pool holds it, mirroring ``Arc::strong_count == 1``) AND idle
        for at least ``idle_ttl`` (``now - last_handout >= idle_ttl``).
        Removing the entry drops the pool's reference, allowing the connection
        to close once any stray handle also releases. Returns the number of
        connections evicted.

        Evictable keys are collected before deletion: Python forbids mutating
        a dict during iteration (Rust's ``DashMap::retain`` mutates under a
        per-shard lock, which has no direct Python equivalent).
        """
        now = time.monotonic()
        evicted: list[ConnKey] = []
        for key, pooled in self._connections.items():
            idle_for = now - pooled.last_handout
            unused = pooled.in_use == 0
            if unused and idle_for >= idle_ttl:
                evicted.append(key)
        for key in evicted:
            del self._connections[key]
        for _ in evicted:
            _metrics_pool_connections_dec()
            _metrics_pool_evictions_inc()
        return len(evicted)

    def spawn_idle_reaper(
        self,
        idle_ttl: float,
        sweep_interval: float,
    ) -> asyncio.Task[None]:
        """Spawn a background sweep task that runs every ``sweep_interval`` (R143).

        Closes connections idle longer than ``idle_ttl``. The task holds a
        :class:`weakref.ref` to the pool, so it exits on its own once the last
        strong reference drops (it never keeps the pool alive -- mirrors
        Rust's ``Arc::downgrade``). The first interval tick is skipped so a
        freshly-handed-out connection is never swept on the immediate tick.
        """
        weak = weakref.ref(self)

        async def _reaper() -> None:
            # ``tokio::time::interval`` resolves its first tick immediately;
            # skip it (mirrors ``ticker.tick().await`` before the loop).
            await asyncio.sleep(sweep_interval)
            while True:
                await asyncio.sleep(sweep_interval)
                pool = weak()
                if pool is None:
                    break  # pool gone -> reaper exits (mirrors weak.upgrade() = None)
                pool.sweep_idle(idle_ttl)

        return asyncio.create_task(_reaper())


# Process-wide shared pool singleton (mirrors Rust ``OnceCell``). ``None``
# until first-use init; asyncio is single-threaded so the first-use read+init
# is atomic (no concurrent caller can interleave mid-init), exactly the
# invariant R142's ``global_semaphore`` relies on.
_SHARED: HubConnectionPool | None = None


async def shared() -> HubConnectionPool:
    """Return the process-wide shared pool, lazily initialising it (R143).

    The shared pool spawns an idle reaper exactly once (see
    :meth:`HubConnectionPool.spawn_idle_reaper`), so a connection that is
    unused and has not been handed out for :data:`DEFAULT_POOL_IDLE_TTL` is
    closed instead of living for the whole process lifetime. Subsequent
    callers observe the same pool. Tests use :meth:`HubConnectionPool.new` to
    avoid cross-test pollution from this singleton.
    """
    global _SHARED
    if _SHARED is None:
        pool = HubConnectionPool.new()
        pool.spawn_idle_reaper(DEFAULT_POOL_IDLE_TTL, DEFAULT_POOL_SWEEP_INTERVAL)
        _SHARED = pool
    return _SHARED
