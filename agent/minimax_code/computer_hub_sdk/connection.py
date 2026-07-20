"""Connection actor structural + network-request layer (R151-R152, SDK leaf 18b-18c).

Forward-port of grok-build ``xai-computer-hub-sdk/src/connection.rs`` lines
348-766: the :class:`ConnectionConfig` consumed by ``connect``, the
:class:`HubConnectionInner` shared-state block, the :class:`HubConnection`
handle, the pure-logic accessor methods (``key`` / ``kind`` / ``actor_id``
/ ``connection_id`` / ``supports`` / ``demux`` / ``take_early_notifications``
/ ``force_reconnect`` / ``await_shutdown`` / ``request_shutdown`` /
``track_session`` / ``untrack_session`` / ``try_send_outbound`` /
``try_alloc_request_id`` / ``bound_session_count``), AND the network-bound
request path (``call_request`` / ``call_request_with_timeout`` /
``call_request_with_deadline`` / ``send_outbound``, lines 671-766). The actor's
socket lifecycle (``connect`` / ``serve``), the ``run_writer`` /
``run_reader_actor`` / ``open_socket`` / ``run_handshake`` spawn pipeline, and
the ``WriterControl<S>`` state machine (lines 806-1382) are later leaves
(R153+).

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
import json
import logging
import threading
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, TypeVar

from minimax_code.computer_hub_sdk.auth import AuthProvider
from minimax_code.computer_hub_sdk.connection_types import (
    SERVE_ATTEMPT_TIMEOUT,
    SERVE_MAX_ATTEMPTS,
    CloseFrame,
    ConnectCallback,
    ConnectionTuning,
    ConnHealth,
    ConnKey,
    DeadlineCallError,
    DisconnectCallback,
    DisconnectCause,
    OtherError,
    ReconnectCallback,
    TimedOut,
    WriteErrorSlot,
    waiter_guard,
)
from minimax_code.computer_hub_sdk.demux import Demux, _MpscClosed, _Sink
from minimax_code.computer_hub_sdk.error import (
    BackpressureError,
    ClientError,
    NetworkError,
    SerdeError,
)
from minimax_code.computer_hub_sdk.metrics import serve_replay_timeout
from minimax_code.computer_hub_sdk.refcount import RefCountedSet
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.envelope import (
    JsonRpcIdString,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
    ResponseResult,
)
from minimax_code.tool_protocol.frames import ServeParams, ServeResult, serve_result_from_wire
from minimax_code.tool_protocol.ids import ConnectionId, RequestId, ServerId, SessionId
from minimax_code.tool_protocol.methods import Method

__all__ = [
    "ConnectionConfig",
    "HubConnectionInner",
    "HubConnection",
]

_LOGGER = logging.getLogger(__name__)


# Bounded backpressure wait before the outbound mpsc is declared full (Rust
# ``Duration::from_millis(250)`` on the ``send`` fallback, line 754). Exposed as
# a named constant so tests can shorten it instead of sleeping 250ms per case.
_OUTBOUND_BACKOFF_WAIT_S = 0.25


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

    # -----------------------------------------------------------------
    # Network-bound request path (Rust async methods, lines 671-766, R152).
    # -----------------------------------------------------------------
    async def call_request(
        self,
        request_id: RequestId,
        request: JsonRpcRequest,
    ) -> JsonRpcResponse:
        """Send a request and await its correlated response (Rust lines 671-682).

        Serialises ``request`` to compact JSON, parks a response waiter on the
        demux keyed by ``request_id``, sends the frame, then awaits the
        one-shot future the demux fulfils when the matching response (or a
        connection-level failure) arrives. The :func:`waiter_guard` scope guard
        drains the parked waiter on any exit path so a late response cannot
        resolve a future the caller already abandoned.
        """
        try:
            text = json.dumps(request.to_wire(), separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise SerdeError(str(exc)) from exc
        fut: asyncio.Future[JsonRpcResponse] = asyncio.Future()
        self._inner.demux.register_response_waiter(request_id, fut)
        with waiter_guard(self._inner.demux, request_id):
            await self.send_outbound(text)
            return await fut

    async def call_request_with_timeout(
        self,
        request_id: RequestId,
        request: JsonRpcRequest,
        timeout: float,
    ) -> JsonRpcResponse:
        """Deadline-bounded ``call_request`` surfacing :class:`ClientError` (684-691).

        Delegates to :meth:`_call_request_with_deadline` and flattens the
        two-variant :class:`DeadlineCallError` into a :class:`ClientError` via
        :meth:`DeadlineCallError.to_client_error` (Rust
        ``impl From<DeadlineCallError> for ClientError``).
        """
        outcome = await self._call_request_with_deadline(request_id, request, timeout)
        if isinstance(outcome, DeadlineCallError):
            # The deadline error is a value (Rust enum), not an exception: only
            # the converted ClientError is raised (impl From<DeadlineCallError>).
            raise outcome.to_client_error() from None
        return outcome

    async def _call_request_with_deadline(
        self,
        request_id: RequestId,
        request: JsonRpcRequest,
        timeout: float,
    ) -> JsonRpcResponse | DeadlineCallError:
        """Deadline-bounded request core returning :class:`DeadlineCallError` (693-746).

        Mirrors the Rust ``match tokio::time::timeout(timeout, rx).await`` whose
        three arms are values of the ``DeadlineCallError`` enum (not exceptions):
        a successful response resolves the future; ``asyncio.wait_for`` raising
        the builtin :class:`TimeoutError` on elapse maps to :class:`TimedOut`; a
        connection-level failure the demux surfaces via ``fut.set_exception``
        (or a send-side failure) is wrapped as :class:`OtherError` (Rust
        ``Ok(Ok(result)) => result.map_err(DeadlineCallError::Other)``). The
        caller (:meth:`call_request_with_timeout`) flattens the value into a
        :class:`ClientError`; this method itself never raises the dataclass.
        """
        try:
            text = json.dumps(request.to_wire(), separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            return OtherError(error=SerdeError(str(exc)))
        fut: asyncio.Future[JsonRpcResponse] = asyncio.Future()
        self._inner.demux.register_response_waiter(request_id, fut)
        with waiter_guard(self._inner.demux, request_id):
            try:
                await self.send_outbound(text)
            except ClientError as exc:
                return OtherError(error=exc)
            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            except TimeoutError:
                return TimedOut(timeout=timeout)
            except ClientError as exc:
                return OtherError(error=exc)

    async def send_outbound(self, text: str) -> None:
        """Enqueue an outbound frame with a bounded backpressure wait (748-766).

        Fast path: :meth:`_Sink.try_send` succeeds when the buffer has room. On
        full, awaits a free slot for up to :data:`_OUTBOUND_BACKOFF_WAIT_S`
        (Rust ``tokio::time::timeout(250ms, send)``); a still-full channel after
        the wait is :class:`BackpressureError`, a closed channel (either the
        try_send or the send arm) is :class:`NetworkError`.
        """
        try:
            self._inner.outbound_tx.try_send(text)
            return
        except asyncio.QueueFull:
            # Fall through to the bounded async wait.
            pass
        except _MpscClosed as exc:
            raise NetworkError("outbound channel closed") from exc
        try:
            await asyncio.wait_for(
                self._inner.outbound_tx.send(text),
                timeout=_OUTBOUND_BACKOFF_WAIT_S,
            )
        except TimeoutError as exc:
            raise BackpressureError("outbound mpsc full beyond bounded wait") from exc
        except _MpscClosed as exc:
            raise NetworkError("outbound channel closed") from exc

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

    async def serve(self, session_id: SessionId, params: ServeParams) -> ServeResult:
        """Send a ``serve`` frame: full tool snapshot for a session (806-851).

        Idempotent — re-sending replaces the tool set for ``session_id``. The
        hub diffs against the previous snapshot and emits ``tools_changed`` to
        subscribed harnesses; this method returns the diff the hub applied.

        Retries up to :data:`~minimax_code.computer_hub_sdk.connection_types.SERVE_MAX_ATTEMPTS`
        times on :class:`TimedOut` (each timeout bumps the
        ``serve_replay_timeout`` counter, warns, and records the deadline error
        as ``last_err``). Any other failure surfaces immediately:
        :class:`OtherError` (a connection-level failure the demux raised) and a
        JSON-RPC error response both abort at once, and a serde failure on the
        result payload raises :class:`SerdeError`. When every bounded attempt
        times out, :meth:`force_reconnect` is called to restart replay and the
        last timeout error is re-raised — a stuck replay is the one serve
        failure mode worth a reconnect, since the snapshot is idempotent.
        """
        last_err: ClientError | None = None
        for _ in range(SERVE_MAX_ATTEMPTS):
            request_id = self.try_alloc_request_id()
            req = JsonRpcRequest(
                jsonrpc=JsonRpcVersion(),
                id=JsonRpcIdString.from_request_id(request_id),
                method=Method.Serve.as_wire_str(),
                params=params,
                session_id=session_id,
            )
            outcome = await self._call_request_with_deadline(
                request_id, req, SERVE_ATTEMPT_TIMEOUT
            )
            if isinstance(outcome, DeadlineCallError):
                if isinstance(outcome, TimedOut):
                    # Rust: metrics.serve_replay_timeout(); warn; last_err = TimedOut.into().
                    serve_replay_timeout()
                    _LOGGER.warning(
                        "serve attempt timed out; will retry (session=%s timeout=%ss)",
                        session_id,
                        outcome.timeout,
                    )
                    last_err = outcome.to_client_error()
                    continue
                # OtherError: surface immediately (Rust ``Err(Other(e)) => return Err(e)``).
                raise outcome.error
            # Ok(resp): dispatch on the response outcome (Result vs Error).
            resp_outcome = outcome.outcome
            if isinstance(resp_outcome, ResponseResult):
                try:
                    return serve_result_from_wire(resp_outcome.value)
                except (KeyError, TypeError, ValueError) as exc:
                    raise SerdeError(str(exc)) from exc
            raise ClientError.from_jsonrpc_error(resp_outcome.error)
        # Every bounded attempt timed out: force reconnect to restart replay.
        _LOGGER.warning(
            "serve timed out on every bounded attempt; forcing reconnect (session=%s)",
            session_id,
        )
        self.force_reconnect()
        if last_err is not None:
            raise last_err
        raise NetworkError("serve failed after bounded retries")

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


# ===========================================================================
# Steady-state control layer (R154, SDK leaf 18e -- connection.rs 961-1036).
# ===========================================================================
# ``ConnectedExit`` / ``exit_for_close_code`` classify how the reader's steady-
# state loop terminates; ``WriterControl<S>`` is the signal the reader sends the
# writer to pause/resume outbound draining across a reconnect; ``now_unix_millis``
# stamps wall-clock instants for those signals. The spawn pipeline that consumes
# them (``run_reader_actor`` / ``run_writer``, connection.rs 1047+) lands in a
# later leaf (R155+); these four are pure-logic support -- no socket / URL / demux
# dependency -- so they port as data + a classify function and unit-test alone.
class ConnectedExit:
    """Tag base for the reader steady-state loop's three exit classes.

    Rust models this as ``enum ConnectedExit { Stop, SocketClosed(DisconnectCause),
    TerminalClose(u16) }``; Python mirrors it as an open family of frozen
    dataclasses dispatched via ``isinstance`` (the Rust ``match`` equivalent).
    """

    __slots__ = ()


@dataclass(frozen=True)
class Stop(ConnectedExit):
    """Actor terminates (shutdown requested by the handle)."""


@dataclass(frozen=True)
class SocketClosed(ConnectedExit):
    """Actor enters the reconnect driver (carries the disconnect cause)."""

    cause: DisconnectCause


@dataclass(frozen=True)
class TerminalClose(ConnectedExit):
    """Terminal close (4100-4199): do not reconnect, surface the code."""

    code: int


def now_unix_millis() -> int:
    """Current wall-clock ms since the Unix epoch (Rust ``now_unix_millis``).

    ``SystemTime::now().duration_since(UNIX_EPOCH)`` -> ``datetime.now(UTC)``;
    the ``unwrap_or(0)`` (clock before epoch, impossible in practice) becomes a
    ``max(0, ...)`` clamp. Returns a non-negative int (Python ``int`` is
    unbounded, but the value fits u64 for the next several hundred years).
    """
    delta_ms = int(datetime.now(UTC).timestamp() * 1000)
    return delta_ms if delta_ms >= 0 else 0


def exit_for_close_code(code: int | None) -> ConnectedExit:
    """Classify a WS close code into a steady-state exit (Rust ``exit_for_close_code``).

    Codes in the terminal band 4100-4199 -> :class:`TerminalClose` (the reader
    must NOT reconnect; the server signalled a permanent condition). Anything
    else -- ``None`` (a close without a code), or a normal/transport close code
    -- -> :class:`SocketClosed` carrying a :class:`CloseFrame` with the original
    code, so the reconnect driver runs.
    """
    if code is not None and 4100 <= code <= 4199:
        return TerminalClose(code)
    return SocketClosed(CloseFrame(code))


# ``S`` is the sink type the writer drains into (a WebSocket split-sink in Rust,
# an opaque write handle here). Bound only at the ``Resume(sink)`` construction
# site; the variants dispatch via ``isinstance`` like the other sum-type bases.
S = TypeVar("S")


class WriterControl(Generic[S]):
    """Tag base for the two writer flow-control signals.

    Rust models this as ``enum WriterControl<S> { Pause, Resume(S) }``; Python
    mirrors it as an open generic family of frozen dataclasses dispatched via
    ``isinstance``. The reader task sends these to the writer task across a
    reconnect: ``Pause`` when the socket dies (stop draining outbound),
    ``Resume(sink)`` once a fresh socket is installed (resume draining).
    """

    __slots__ = ()


@dataclass(frozen=True)
class Pause(WriterControl[S]):
    """Socket is dead; the writer must stop draining ``outbound_rx``."""


@dataclass(frozen=True)
class Resume(WriterControl[S]):
    """Reconnected; install ``sink`` and resume draining ``outbound_rx``."""

    sink: S
