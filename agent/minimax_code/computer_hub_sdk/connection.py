"""Connection actor structural layer (R151, SDK leaf 18b).

Forward-port of grok-build ``xai-computer-hub-sdk/src/connection.rs`` lines
348-857: the :class:`ConnectionConfig` consumed by ``connect``, the
:class:`HubConnectionInner` shared-state block, the :class:`HubConnection`
handle, AND the pure-logic accessor methods (``key`` / ``kind`` / ``actor_id``
/ ``connection_id`` / ``supports`` / ``demux`` / ``take_early_notifications``
/ ``force_reconnect`` / ``await_shutdown`` / ``request_shutdown`` /
``track_session`` / ``untrack_session`` / ``try_send_outbound`` /
``try_alloc_request_id`` / ``bound_session_count``). The actor's network-bound
methods (``connect`` / ``call_request`` / ``call_request_with_deadline`` /
``send_outbound`` / ``serve``), the ``run_writer`` / ``run_reader_actor`` /
``open_socket`` / ``run_handshake`` spawn pipeline, and the ``WriterControl<S>``
state machine (lines 961-1382) are later leaves (R152+).

tokio -> asyncio / Rust -> Python adaptations (no behavior change):

* ``Arc<HubConnectionInner>`` shared state -> a single
  :class:`HubConnectionInner` instance. Python's reference counting replaces
  the explicit ``Arc`` -- the handle, the reader task, the writer task, and
  the liveness probe share one instance by reference, and the
  interior-mutability primitives become ``threading.Lock``-guarded slots.
* ``mpsc::Sender<()>`` stop / reconnect + ``mpsc::Sender<String>`` outbound ->
  :class:`_Sink` (R149). ``try_send`` raises :class:`_MpscClosed` (receiver
  gone) or :class:`asyncio.QueueFull` (at capacity). The Rust ``let _ =``
  discard on the stop / reconnect paths is ``try/except`` here.
* ``Arc<Mutex<Option<ConnectionId>>>`` -> :class:`_ConnectionIdSlot`
  (``threading.Lock`` + ``Optional``). Rust ``connection_id`` is ``async`` for
  the ``tokio::Mutex``; under the GIL the Python read is synchronous, so the
  method is sync (no await needed) -- the API doc notes the parity.
* ``parking_lot::RwLock<Vec<String>>`` hello capabilities -> :class:`_HelloCaps`
  (``threading.Lock`` + ``list``; single-threaded asyncio needs no real RW
  lock, but the lock mirrors serialization for cross-thread defense).
* ``std::sync::atomic::AtomicU64`` next_request_id -> :class:`_AtomicCounter`
  (``threading.Lock`` + ``int``; ``fetch_add`` returns the previous value).
* ``tokio_util::sync::CancellationToken`` shutdown -> :class:`asyncio.Event`
  (R138 mapping): ``cancel()`` -> ``set``; ``cancelled().await`` ->
  ``await Event.wait``.
* ``parking_lot::Mutex<Option<broadcast::Receiver<Value>>>`` early_notif_rx ->
  :class:`_EarlyNotifSlot` (``threading.Lock`` + ``Optional[Queue]``; the
  receiver is the ``asyncio.Queue`` returned by
  :meth:`_NotificationBroadcast.subscribe`).
* ``Arc<dyn AuthProvider>`` credential -> :class:`AuthProvider` Protocol (R139).
* ``Weak<HubConnectionPool>`` on_fatal -> :class:`weakref.ref` (the
  pool<->connection edge is not an ownership cycle).
* ``impl Drop for HubConnection`` -> ``__del__`` (best-effort ``try_send``;
  Python finalisation is non-deterministic, so :meth:`request_shutdown` is the
  canonical path).

YAGNI boundary: the connection is *constructed* by an actor-internal path in a
later leaf (R152+); this module defines the type + accessors but not the
network ``connect``. Tests build a :class:`HubConnectionInner` directly with
mock :class:`_Sink` / :class:`Demux` / :class:`RefCountedSet` and wrap it in a
:class:`HubConnection`.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from dataclasses import dataclass, field

from minimax_code.computer_hub_sdk.auth import AuthProvider
from minimax_code.computer_hub_sdk.connection_types import (
    ConnectCallback,
    ConnectionTuning,
    ConnHealth,
    ConnKey,
    DisconnectCallback,
    ReconnectCallback,
    WriteErrorSlot,
)
from minimax_code.computer_hub_sdk.demux import Demux, _MpscClosed, _Sink
from minimax_code.computer_hub_sdk.error import BackpressureError, NetworkError
from minimax_code.computer_hub_sdk.refcount import RefCountedSet
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.ids import ConnectionId, RequestId, ServerId, SessionId

__all__ = [
    "ConnectionConfig",
    "HubConnectionInner",
    "HubConnection",
]


# ===========================================================================
# Interior-mutability slot helpers.
# ===========================================================================
class _ConnectionIdSlot:
    """``Arc<Mutex<Option<ConnectionId>>>`` -> lock-guarded optional.

    The cached server-issued connection id; updated on every (re)connect
    handshake, read by :meth:`HubConnection.connection_id`. Single-threaded
    asyncio makes a ``threading.Lock`` sufficient (defensive across the
    liveness-probe thread).
    """

    __slots__ = ("_value", "_lock")

    def __init__(self, value: ConnectionId | None = None) -> None:
        self._value = value
        self._lock = threading.Lock()

    def get(self) -> ConnectionId | None:
        with self._lock:
            return self._value

    def set(self, value: ConnectionId) -> None:
        with self._lock:
            self._value = value

    def clear(self) -> None:
        with self._lock:
            self._value = None


class _HelloCaps:
    """``parking_lot::RwLock<Vec<String>>`` server-advertised capabilities.

    Refreshed on every (re)connect handshake. Empty when the ack carried none
    (indistinguishable from a server predating the field), which
    :meth:`HubConnection.supports` surfaces as ``None`` (unknown).
    """

    __slots__ = ("_caps", "_lock")

    def __init__(self, caps: list[str] | None = None) -> None:
        self._caps = list(caps) if caps else []
        self._lock = threading.Lock()

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._caps) == 0

    def contains(self, capability: str) -> bool:
        with self._lock:
            return any(c == capability for c in self._caps)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._caps)

    def replace(self, caps: list[str]) -> None:
        with self._lock:
            self._caps = list(caps)

    def clear(self) -> None:
        with self._lock:
            self._caps = []


class _AtomicCounter:
    """``std::sync::atomic::AtomicU64`` monotonic request-id counter.

    ``fetch_add`` returns the previous value and stores previous + n. Relaxed
    ordering -- the counter guards per-connection uniqueness, no cross-field
    memory ordering is needed.
    """

    __slots__ = ("_value", "_lock")

    def __init__(self, value: int = 0) -> None:
        self._value = value
        self._lock = threading.Lock()

    def fetch_add(self, n: int = 1) -> int:
        with self._lock:
            prev = self._value
            self._value += n
            return prev


class _EarlyNotifSlot:
    """``parking_lot::Mutex<Option<broadcast::Receiver<Value>>>`` early-notif rx.

    Holds the broadcast receiver parked during the pre-hello window so
    server-firehose notifications arriving before the first ``hello_ack`` are
    not lost. ``take`` drains it (the actor consumes it once, on hello).
    """

    __slots__ = ("_value", "_lock")

    def __init__(self, value: asyncio.Queue | None = None) -> None:
        self._value = value
        self._lock = threading.Lock()

    def take(self) -> asyncio.Queue | None:
        with self._lock:
            value = self._value
            self._value = None
            return value

    def set(self, value: asyncio.Queue) -> None:
        with self._lock:
            self._value = value

    def get(self) -> asyncio.Queue | None:
        with self._lock:
            return self._value


# ===========================================================================
# ConnectionConfig (lines 376-423).
# ===========================================================================
@dataclass
class ConnectionConfig:
    """Configuration for a :class:`HubConnection` (Rust struct ConnectionConfig).

    Consumed by ``connect``; not frozen (the pool builds a fresh config per
    reconnect attempt rather than cloning). ``url`` is a plain ``str`` (Rust
    ``Url``); ``server_metadata`` is a JSON object dict (Rust
    ``serde_json::Value``); ``on_fatal`` is a ``weakref.ref`` to the owning
    pool (Rust ``Weak<HubConnectionPool>``), ``None`` for the unpooled path.
    """

    # ``ws://`` / ``wss://`` URL of the server (Rust ``Url`` -> ``str``).
    url: str
    credential: AuthProvider
    kind: ConnectionKind
    on_reconnect: ReconnectCallback | None = None
    on_disconnect: DisconnectCallback | None = None
    # Fired once on the initial successful connect before the actor starts.
    on_connect: ConnectCallback | None = None
    server_id: ServerId | None = None
    server_description: str | None = None
    server_metadata: dict[str, object] | None = None
    outbound_buffer: int | None = None
    tuning: ConnectionTuning = field(default_factory=ConnectionTuning)
    alpha_test_key: str | None = None
    allow_insecure_ws: bool = False
    on_fatal: weakref.ref | None = None


# ===========================================================================
# HubConnectionInner (lines 435-482).
# ===========================================================================
class HubConnectionInner:
    """Per-connection shared state (Rust struct HubConnectionInner).

    In Rust this lives behind ``Arc<HubConnectionInner>`` so the handle, the
    reader task, the writer task, and the liveness probe share one block with
    interior mutability. Python's reference counting removes the need for an
    explicit ``Arc``: one instance is shared by reference, and the
    interior-mutability locks (``Arc<Mutex<T>>`` etc.) become
    ``threading.Lock``-guarded slots.

    The configuration fields (key/kind/credential/on_*/server_*/...) are
    required at construction; the runtime-state fields (connection_id slot,
    hello caps, request-id counter, shutdown event, health, writer-error slot)
    default to fresh instances. ``connect`` (R152+) would normally build this
    from a :class:`ConnectionConfig` + freshly-allocated channels; tests
    construct it directly with mock channels.
    """

    __slots__ = (
        "key",
        "kind",
        "credential",
        "on_reconnect",
        "on_disconnect",
        "server_id",
        "server_description",
        "server_metadata",
        "alpha_test_key",
        "allow_insecure_ws",
        "on_fatal",
        "reconnect_backoff",
        "outbound_tx",
        "demux",
        "bound_sessions",
        "connection_id",
        "hello_capabilities",
        "next_request_id",
        "shutdown",
        "stop_tx",
        "reconnect_tx",
        "early_notif_rx",
        "health",
        "writer_error",
    )

    def __init__(
        self,
        *,
        key: ConnKey,
        kind: ConnectionKind,
        credential: AuthProvider,
        reconnect_backoff: tuple[float, ...],
        outbound_tx: _Sink,
        demux: Demux,
        bound_sessions: RefCountedSet[SessionId],
        stop_tx: _Sink,
        reconnect_tx: _Sink,
        on_reconnect: ReconnectCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
        server_id: ServerId | None = None,
        server_description: str | None = None,
        server_metadata: dict[str, object] | None = None,
        alpha_test_key: str | None = None,
        allow_insecure_ws: bool = False,
        on_fatal: weakref.ref | None = None,
        connection_id: _ConnectionIdSlot | None = None,
        hello_capabilities: _HelloCaps | None = None,
        next_request_id: _AtomicCounter | None = None,
        shutdown: asyncio.Event | None = None,
        early_notif_rx: asyncio.Queue | None = None,
        health: ConnHealth | None = None,
        writer_error: WriteErrorSlot | None = None,
    ) -> None:
        # Required configuration / channel state.
        self.key = key
        self.kind = kind
        self.credential = credential
        self.reconnect_backoff = reconnect_backoff
        self.outbound_tx = outbound_tx
        self.demux = demux
        self.bound_sessions = bound_sessions
        self.stop_tx = stop_tx
        self.reconnect_tx = reconnect_tx
        # Optional configuration.
        self.on_reconnect = on_reconnect
        self.on_disconnect = on_disconnect
        self.server_id = server_id
        self.server_description = server_description
        self.server_metadata = server_metadata
        self.alpha_test_key = alpha_test_key
        self.allow_insecure_ws = allow_insecure_ws
        self.on_fatal = on_fatal
        # Runtime state: fresh defaults if unset.
        self.connection_id = connection_id if connection_id is not None else _ConnectionIdSlot()
        self.hello_capabilities = (
            hello_capabilities if hello_capabilities is not None else _HelloCaps()
        )
        self.next_request_id = (
            next_request_id if next_request_id is not None else _AtomicCounter()
        )
        self.shutdown = shutdown if shutdown is not None else asyncio.Event()
        self.early_notif_rx = _EarlyNotifSlot(early_notif_rx)
        self.health = health if health is not None else ConnHealth()
        self.writer_error = writer_error if writer_error is not None else WriteErrorSlot()


# ===========================================================================
# HubConnection (lines 360-370, 484-857 -- pure-logic accessor subset).
# ===========================================================================
class HubConnection:
    """A live (or reconnecting) connection handle (Rust struct HubConnection).

    Cheap to share: the accessor methods are ``&self``-equivalent, so multiple
    consumers reference the same :class:`HubConnectionInner` without external
    locking. Dropping the last reference (or calling
    :meth:`request_shutdown`) signals the actor to stop and drain in-flight
    waiters with :class:`NetworkError`. The actor's network half
    (``connect`` / ``call_request`` / ``serve`` / ``send_outbound``) lands in
    R152+; this layer defines the handle + the pure-logic accessors.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: HubConnectionInner) -> None:
        self._inner = inner

    def __repr__(self) -> str:
        # Mirrors Rust Debug: key + kind + finish_non_exhaustive.
        return f"HubConnection(key={self._inner.key!r}, kind={self._inner.kind!r}, ...)"

    # -----------------------------------------------------------------
    # Pure-logic accessors (Rust ``&self`` methods, lines 596-800).
    # -----------------------------------------------------------------
    def key(self) -> ConnKey:
        """Pool dedup key for this connection."""
        return self._inner.key

    def kind(self) -> ConnectionKind:
        """Connection role (host / tool-server)."""
        return self._inner.kind

    def actor_id(self) -> int:
        """Stable identity of this connection's actor state.

        Rust uses ``Arc::as_ptr(&self.inner) as *const () as usize``; Python's
        :func:`id` returns the instance's memory identity, which is equally
        stable for the instance lifetime and serves the same
        pool-eviction-by-identity purpose.
        """
        return id(self._inner)

    def connection_id(self) -> ConnectionId | None:
        """Server-issued id of the most recent post-handshake socket.

        Rust is ``async`` for the ``tokio::Mutex``; under the GIL the
        ``threading.Lock`` read is synchronous (no await needed), so this
        method is sync. During a reconnect gap this still names the dropped
        connection until the next handshake + replay completes.
        """
        return self._inner.connection_id.get()

    def supports(self, capability: str) -> bool | None:
        """Whether the server advertised ``capability`` in the latest hello_ack.

        * ``True``: advertised.
        * ``False``: the ack carried a non-empty list that excludes it.
        * ``None``: the ack advertised nothing -- servers predating the
          ``capabilities`` field are indistinguishable from an empty list, so
          callers should probe per call.
        """
        caps = self._inner.hello_capabilities
        if caps.is_empty():
            return None
        return caps.contains(capability)

    def demux(self) -> Demux:
        """Inbound demux state (response waiters + session inboxes)."""
        return self._inner.demux

    def take_early_notifications(self) -> asyncio.Queue | None:
        """Take (drain) the early-notification receiver, if set.

        The actor consumes it once, on hello, so pre-hello firehose
        notifications are not lost.
        """
        return self._inner.early_notif_rx.take()

    def force_reconnect(self) -> None:
        """Signal the actor to reconnect (best-effort; drop on closed/full).

        Mirrors ``let _ = self.inner.reconnect_tx.try_send(())`` -- a pending
        reconnect signal is already queued, so a full/closed channel is silent.
        """
        try:
            self._inner.reconnect_tx.try_send(None)
        except (_MpscClosed, asyncio.QueueFull):
            pass

    async def await_shutdown(self) -> None:
        """Resolve once the actor has fully exited.

        ``CancellationToken::cancelled().await`` -> ``await Event.wait`` (R138).
        The event has persistent semantics: a wait arriving after the actor has
        already cancelled resolves immediately.
        """
        await self._inner.shutdown.wait()

    def request_shutdown(self) -> None:
        """Begin shutdown: actor drains waiters with NetworkError and exits.

        Idempotent (redundant calls are no-ops). Equivalent to dropping the
        last reference, but lets a holder trigger shutdown without giving up
        its reference. The outbound channel closes shortly after, so
        subsequent ``send_outbound`` calls return :class:`NetworkError`.
        """
        try:
            self._inner.stop_tx.try_send(None)
        except (_MpscClosed, asyncio.QueueFull):
            pass

    def track_session(self, session_id: SessionId) -> None:
        """Increment the refcount on ``session_id``.

        Tracked locally for reconnect-replay; the server learns about it via
        ``serve`` (auto-registration on the server side).
        """
        self._inner.bound_sessions.increment(session_id)

    def untrack_session(self, session_id: SessionId) -> None:
        """Decrement the refcount on ``session_id`` (removes tracking at zero)."""
        self._inner.bound_sessions.decrement(session_id)

    def try_send_outbound(self, text: str) -> None:
        """Non-blocking outbound enqueue for synchronous drop paths.

        Raises :class:`BackpressureError` if the channel is full, or
        :class:`NetworkError` if it is closed. Mirrors the Rust
        ``Result<(), ClientError>`` -- callers that want to abandon-on-full
        (the heartbeat-pong drop discipline) swallow the exception.
        """
        try:
            self._inner.outbound_tx.try_send(text)
        except asyncio.QueueFull as exc:
            raise BackpressureError("outbound mpsc full") from exc
        except _MpscClosed as exc:
            raise NetworkError("outbound channel closed") from exc

    def try_alloc_request_id(self) -> RequestId:
        """Allocate a fresh monotonic request id (``c{value}``).

        Rust returns ``Result`` for a future-added invariant; the ``c{value}``
        format can never produce an empty string today, so this is infallible
        in practice -- the ``try_`` prefix is retained for API parity.
        """
        value = self._inner.next_request_id.fetch_add()
        return RequestId(f"c{value}")

    def bound_session_count(self) -> int:
        """Number of distinct sessions currently bound to this connection.

        Stable observable for monitoring and tests; not on the hot path.
        """
        return len(self._inner.bound_sessions)

    def __del__(self) -> None:
        """Best-effort stop signal on finalisation (Rust ``impl Drop``).

        Python finalisation is non-deterministic; :meth:`request_shutdown` is
        the canonical path. The ``try_send`` is guarded so a GC during
        interpreter teardown (loop/sink already gone) cannot raise.
        """
        try:
            self._inner.stop_tx.try_send(None)
        except Exception:
            pass
