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
import time
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, Protocol, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from minimax_code.computer_hub_sdk.auth import AuthCredential, AuthProvider
from minimax_code.computer_hub_sdk.connection_types import (
    CLOCK_PROBE_INTERVAL,
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
    Eof,
    Forced,
    LivenessDeadline,
    OtherError,
    OutageInfo,
    ReadError,
    ReconnectCallback,
    TimedOut,
    WriteError,
    WriteErrorSlot,
    reconnect_attempt_budget,
    waiter_guard,
)
from minimax_code.computer_hub_sdk.demux import Demux, _MpscClosed, _Sink, _SinkRx
from minimax_code.computer_hub_sdk.error import (
    AuthError,
    BackpressureError,
    ClientError,
    Closed,
    HandshakeAuthFailed,
    InsecureScheme,
    InvalidConfig,
    NetworkError,
    SerdeError,
)
from minimax_code.computer_hub_sdk.metrics import (
    reconnect_duration_observe,
    reconnect_failed,
    reconnect_succeeded,
    reconnect_writer_resume,
    serve_replay_timeout,
)
from minimax_code.computer_hub_sdk.refcount import RefCountedSet
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.envelope import (
    JsonRpcIdString,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
    ResponseResult,
)
from minimax_code.tool_protocol.frames import (
    PongFrame,
    ServeParams,
    ServeResult,
    serve_result_from_wire,
)
from minimax_code.tool_protocol.ids import ConnectionId, RequestId, ServerId, SessionId
from minimax_code.tool_protocol.methods import Method
from minimax_code.tracing.http_client import attach_trace_to_http_request

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


# ===========================================================================
# Spawn-pipeline pure-logic helpers (R155, SDK leaf 18f -- connection.rs
# 861-869 + 980-994 + 1015-1022 + 1080-1084).
#
# These four functions are the zero-socket / zero-task slice the spawn pipeline
# (``run_writer`` / ``run_reader_actor``, later leaves) factors out so the
# inbound-frame router, the stream-end classifier, and the disconnect callback
# can be unit-tested without a live transport. They take a ``HubConnectionInner``
# and touch only its shared state (``demux`` / ``writer_error`` /
# ``on_disconnect``); the asyncio tasks that drive them land in R156+.
# ===========================================================================
def host_is_loopback(url: str) -> bool:
    """True when ``url``'s host is one of the canonical loopback names.

    Mirrors Rust ``host_is_loopback`` (861-869): ``Ipv4Addr::LOCALHOST``
    (127.0.0.1), ``Ipv6Addr::LOCALHOST`` (::1), or a case-insensitive
    ``localhost`` domain all count; anything else (including a host-less URL
    or a parse failure) is False.
    """
    host = urlparse(url).hostname
    if host is None:
        return False
    return host in ("127.0.0.1", "::1", "localhost")


def route_or_pong(inner: HubConnectionInner, text: str) -> str | None:
    """Decode an inbound text frame (Rust ``route_or_pong``, 980-994).

    A ``ping`` method is answered inline with a fresh ``pong`` (its JSON wire
    form, to be written straight back out the socket); any other decodable
    JSON object is handed to ``inner.demux.route`` and returns ``None``. A
    non-dict JSON value (list / string / number / null) is dropped -- Python's
    ``Demux.route`` is dict-only (``frame.get(...)``), so the non-dict arm maps
    to Rust's terminal ``Unrouted`` outcome. An unparseable frame logs a
    warning and is discarded.
    """
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        _LOGGER.warning("discarding unparseable inbound text frame: %s", exc)
        return None
    if not isinstance(value, dict):
        return None
    if value.get("method") == Method.Ping.as_wire_str():
        return json.dumps(PongFrame(ts_ms=now_unix_millis()).to_wire())
    inner.demux.route(value)
    return None


def classify_stream_end(
    inner: HubConnectionInner, read_error: str | None
) -> DisconnectCause:
    """Classify the cause of an inbound stream ending (Rust 1015-1022).

    Probes :meth:`WriteErrorSlot.take` first: a write-side failure observed by
    the writer task is attributed over the reader's own EOF / read error (the
    slot is drained under lock so it fires at most once per reconnect). With no
    write error, a reader-side ``read_error`` detail becomes a
    :class:`ReadError`; otherwise the stream ended cleanly (:class:`Eof`).
    """
    detail = inner.writer_error.take()
    if detail is not None:
        return WriteError(detail_str=detail)
    if read_error is not None:
        return ReadError(detail_str=read_error)
    return Eof()


def fire_on_disconnect(inner: HubConnectionInner) -> None:
    """Best-effort fire of the optional disconnect callback (Rust 1080-1084).

    The callback is an ``Option<DisconnectCallback>``; ``None`` is a no-op (the
    reader task calls this unconditionally on the way out, whether or not a
    callback was registered).
    """
    if inner.on_disconnect is not None:
        inner.on_disconnect()


# ===========================================================================
# Spawn-pipeline reconnect pure-logic helpers (R156, SDK leaf 18g -- connection.rs
# 1243-1245 + 1377-1382).
#
# The last two zero-socket helpers the spawn pipeline's reconnect loop
# (``run_reader_actor``, R157+) factors out: the backoff-schedule lookup and
# the reconnect-signal drain. Both are pure logic (the drain operates on the
# mpsc buffer synchronously via ``get_nowait`` -- no running loop needed), so
# the reconnect driver's steady-state arms can be unit-tested without a live
# transport.
# ===========================================================================
def backoff_for(attempt: int, schedule: tuple[float, ...]) -> float:
    """Look up the backoff for an attempt index (Rust ``backoff_for``, 1377-1382).

    ``idx = (attempt - 1).min(len - 1)``: attempt 1 is the first slot, the last
    slot is reused for any further attempt (the schedule's tail is the cap), and
    an empty schedule returns ``0.0`` instead of panicking (a degenerate tuning
    override collapses to no delay rather than aborting the reconnect loop).
    ``attempt == 0`` saturates to the first slot (Rust ``u32::saturating_sub``).
    """
    if not schedule:
        return 0.0
    idx = max(attempt - 1, 0)
    return schedule[min(idx, len(schedule) - 1)]


def drain_reconnect_signals(reconnect_rx: _SinkRx[None]) -> None:
    """Drain queued reconnect triggers (Rust ``drain_reconnect_signals``, 1243-1245).

    After a successful reconnect (``run_reader_actor`` line 1184) any reconnect
    signals that piled up during the outage window are discarded so the freshly
    restored connection is not immediately torn down again.

    Unit-channel caveat: every queued signal is ``None`` (Rust ``mpsc<()>>``),
    so :meth:`_SinkRx.try_recv` -- which returns ``None`` for both "a queued
    unit" and "empty" -- cannot distinguish the two. Drain the bounded buffer
    directly via ``get_nowait`` until :class:`asyncio.QueueEmpty`, mirroring
    Rust ``while reconnect_rx.try_recv().is_ok() {}``.
    """
    # Unit-channel drain: try_recv cannot tell a queued ``None`` from "empty",
    # so drain the underlying bounded buffer directly.
    buffer = reconnect_rx._channel.buffer
    while True:
        try:
            buffer.get_nowait()
        except asyncio.QueueEmpty:
            return


# ===========================================================================
# Spawn-pipeline writer task (R157, SDK leaf 18h -- connection.rs 1047-1078).
#
# The writer half of the split connection actor. Generic over the sink so it
# can be unit-tested with an in-memory sink without a live socket (Rust notes
# on ``run_writer``, line 1045). The biased ``tokio::select!`` (stop > ctl >
# ping-if-live > outbound-if-live) is mirrored with ``asyncio.wait`` +
# FIRST_COMPLETED and a declared-order priority check; on a send failure the
# detail lands in the shared ``WriteErrorSlot`` so the reader can classify the
# outage as a write-side fault on reconnect.
# ===========================================================================
class WriterSink(Protocol):
    """Outbound transport abstraction the writer task drains onto.

    Mirrors the ``futures::Sink<Message>`` bound on Rust ``run_writer``
    (connection.rs 1055): two narrow async methods -- text frames and keepalive
    pings -- so the writer task stays generic over the transport and can be
    unit-tested with an in-memory sink. Distinct from
    :class:`~minimax_code.computer_hub_sdk.handshake.HandshakeSink`, which
    swaps the keepalive ping for the handshake pong.
    """

    async def send_text(self, text: str) -> None:
        """Send a text frame; raise on a dead socket so the writer records it."""
        ...

    async def send_ping(self) -> None:
        """Send a keepalive ping; raise on a dead socket so the writer records it."""
        ...


async def run_writer(
    sink: WriterSink,
    outbound_rx: _SinkRx[str],
    writer_ctl_rx: _SinkRx[WriterControl[WriterSink]],
    writer_stop_rx: _SinkRx[None],
    ping_period: float,
    write_error: WriteErrorSlot,
) -> None:
    """Drain outbound text frames + keepalive pings onto ``sink``.

    Forward-port of Rust ``run_writer`` (connection.rs 1047-1078): the writer
    half of the split connection actor. The biased ``tokio::select!``
    (stop > ctl > ping-if-live > outbound-if-live) is mirrored with
    ``asyncio.wait(FIRST_COMPLETED)`` plus a declared-order priority check --
    stop always wins, then control, then (when ``live``) the keepalive ping,
    then the outbound frame. On a send failure the detail is recorded in
    ``write_error`` (the reader probes it on reconnect to classify the outage
    as a write-side fault) and ``live`` flips false until the reader sends
    ``Resume(fresh_sink)``.

    tokio -> asyncio adaptation: Rust ``tokio::select!`` re-polls the same
    bound futures each iteration and drops the non-selected ones; Python has
    no biased multi-await, so each iteration rebuilds the awaitables as tasks,
    ``asyncio.wait`` returns on the first completion, the pending tasks are
    cancelled + reaped, and the completed ones are dispatched in declaration
    order. ``tokio::time::interval`` (whose first tick fires immediately and
    is consumed up front so the loop's first real tick lands one ``ping_period``
    later) maps to ``asyncio.sleep(ping_period)`` directly -- the first sleep
    already waits a full period.
    """
    live = True
    while True:
        stop_task = asyncio.ensure_future(writer_stop_rx.recv())
        ctl_task = asyncio.ensure_future(writer_ctl_rx.recv())
        if live:
            ping_task: asyncio.Future[None] | None = asyncio.ensure_future(
                asyncio.sleep(ping_period)
            )
            out_task: asyncio.Future[str | None] | None = asyncio.ensure_future(
                outbound_rx.recv()
            )
            done, pending = await asyncio.wait(
                (stop_task, ctl_task, ping_task, out_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            ping_task = None
            out_task = None
            done, pending = await asyncio.wait(
                (stop_task, ctl_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        for task in pending:
            task.cancel()
        if pending:
            # Reap cancelled tasks so a return path never strands orphans that
            # asyncio would warn about at loop shutdown.
            await asyncio.gather(*pending, return_exceptions=True)
        # Biased priority: stop > ctl > ping > outbound (Rust select! order).
        if stop_task in done:
            return
        if ctl_task in done:
            ctl = ctl_task.result()
            if ctl is None:
                return
            if isinstance(ctl, Pause):
                live = False
            elif isinstance(ctl, Resume):
                sink = ctl.sink
                live = True
                write_error.take()
            continue
        if live and ping_task is not None and ping_task in done:
            try:
                await sink.send_ping()
            except Exception as exc:
                write_error.set(f"ping send failed: {exc}")
                live = False
            continue
        if live and out_task is not None and out_task in done:
            text = out_task.result()
            if text is None:
                return
            try:
                await sink.send_text(text)
            except Exception as exc:
                write_error.set(f"frame send failed: {exc}")
                live = False
            continue


# ===========================================================================
# tokio::time::interval port (R159, SDK leaf 18i prep -- run_reader_phase
# clock-probe timing infrastructure). Python's asyncio.sleep is a one-shot
# future; recreating it every loop iteration resets the timeline, so a
# clock_probe arm co-scheduled with a busy inbound stream would never fire.
# _Interval remembers the last tick's monotonic instant and returns a future
# that resolves at the next period boundary, preserving the global timeline
# across loop iterations. Delay semantics: a missed tick (the loop was busy
# past the boundary) resolves immediately and snaps last_tick to now rather
# than Burst-catching-up -- refresh_clock is idempotent, so back-to-back
# compensating ticks add no value and would starve the inbound arm.
# ===========================================================================
class _Interval:
    """tokio::time::interval port for run_reader_phase's clock probe.

    Mirrors ``tokio::time::interval(period)`` + ``tick().await``: the first
    tick resolves immediately; subsequent ticks align to the
    ``last_tick + period`` grid. A missed tick (the loop ran past the boundary)
    resolves immediately and resets ``last_tick`` to now (Delay, not Burst) --
    :meth:`ConnHealth.refresh_clock` is idempotent so compensating bursts add
    no value and would starve the inbound stream arm.
    """

    __slots__ = ("_period", "_last_tick")

    def __init__(self, period: float) -> None:
        self._period = period
        self._last_tick: float | None = None

    def tick(self) -> asyncio.Future:
        """Return a future resolving at the next period boundary."""
        now = time.monotonic()
        loop = asyncio.get_running_loop()
        if self._last_tick is None:
            self._last_tick = now
            fut = loop.create_future()
            fut.set_result(None)
            return fut
        target = self._last_tick + self._period
        if now >= target:
            self._last_tick = now
            fut = loop.create_future()
            fut.set_result(None)
            return fut
        self._last_tick = target
        return asyncio.ensure_future(asyncio.sleep(target - now))


# ===========================================================================
# Inbound stream sum-types (R160, SDK leaf 18j -- run_reader_phase wire shapes).
#
# Rust's reader loop consumes a ``Stream<Item = Result<Message, Error>>`` from
# tungstenite; ``stream.next()`` yields ``Option<Result<Message, Error>>``
# (None at stream end). Python has no single crate-agnostic WS stream type, so
# run_reader_phase is generic over an ``AsyncIterator[WsInbound]`` instead: the
# three ``WsInbound`` variants map the Option<Result> payload, and the six
# ``WsMessage`` variants map tungstenite's ``Message`` enum. A transport adapter
# (R161+) bridges a real websockets / aiohttp stream into this shape; the
# pure-logic loop and every dispatch arm unit-test against a fake iterator.
# ===========================================================================
class WsMessage:
    """Tag base for the 6 tungstenite ``Message`` variants the reader handles.

    Rust ``Message`` is a single enum (Text / Ping / Pong / Binary / Close /
    Frame); the reader decodes Text (route), ignores Ping / Pong / Frame
    (tungstenite auto-acks WS-layer keepalive), warns on Binary, and turns Close
    into an exit. Python mirrors it as an open family of frozen dataclasses
    dispatched via ``isinstance``. A WS-layer Ping is distinct from a JSON-RPC
    ``method=ping`` request, which is answered by :func:`route_or_pong`.
    """

    __slots__ = ()


@dataclass(frozen=True)
class WsText(WsMessage):
    """Inbound text frame -- the only variant :func:`route_or_pong` decodes."""

    text: str


@dataclass(frozen=True)
class WsPing(WsMessage):
    """WS-layer keepalive ping (tungstenite auto-acks; the reader ignores it)."""


@dataclass(frozen=True)
class WsPong(WsMessage):
    """WS-layer keepalive pong (informational; the reader ignores it)."""


@dataclass(frozen=True)
class WsBinary(WsMessage):
    """Unexpected binary frame (the protocol is text-only; logged + ignored)."""


@dataclass(frozen=True)
class WsClose(WsMessage):
    """Server-initiated close frame (carries the optional WS close code)."""

    code: int | None


@dataclass(frozen=True)
class WsRaw(WsMessage):
    """Raw tungstenite ``Frame`` (extension data; the reader ignores it)."""


class WsInbound:
    """Tag base for the 3 ``Option<Result<Message, Error>>`` outcomes.

    Maps the three ways ``stream.next()`` resolves: a decoded frame
    (``Some(Ok(msg))``), a transport read error (``Some(Err(e))``), or stream
    end (``None``). Dispatched via ``isinstance`` in :func:`run_reader_phase`.
    """

    __slots__ = ()


@dataclass(frozen=True)
class WsFrameReceived(WsInbound):
    """A decoded inbound frame (Rust ``Some(Ok(Message))``)."""

    message: WsMessage


@dataclass(frozen=True)
class WsReadError(WsInbound):
    """A transport read error (Rust ``Some(Err(e))``; carries the detail)."""

    detail: str


@dataclass(frozen=True)
class WsStreamEnd(WsInbound):
    """The stream finished with no further frame (Rust ``None`` / clean EOF)."""


async def _next_inbound(stream: AsyncIterator[WsInbound]) -> WsInbound:
    """Await the next inbound item, mapping stream end to :class:`WsStreamEnd`.

    Rust's ``stream.next()`` returns ``None`` at EOF; an async iterator raises
    ``StopAsyncIteration`` instead. Wrapping ``anext`` here keeps the biased-
    select arm a single ``ensure_future`` and lets a fake iterator signal
    end-of-stream by simply stopping.
    """
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return WsStreamEnd()


async def run_reader_phase(
    inner: HubConnectionInner,
    stream: AsyncIterator[WsInbound],
    stop_rx: _SinkRx[None],
    reconnect_rx: _SinkRx[None],
    liveness_deadline: float,
) -> ConnectedExit:
    """Reader steady-state loop (Rust ``run_reader_phase``, connection.rs 1259-1299).

    Generic over the inbound ``stream`` so the loop and every dispatch arm unit-
    test without a live transport (mirroring :func:`run_writer`). Five biased-
    select arms, audited in declaration order: ``stop_rx`` (terminal
    :class:`Stop`) > ``reconnect_rx`` (forced reconnect -> ``SocketClosed(Forced)``)
    > ``stream`` (route the frame, rearm the liveness deadline, or exit on
    Close / error / EOF) > ``clock_probe`` (:meth:`ConnHealth.refresh_clock`,
    the 5s wall-vs-mono probe) > ``deadline`` (no inbound frame in time ->
    ``SocketClosed(LivenessDeadline)``).

    tokio -> asyncio: Rust pins ``deadline`` outside the ``select!`` and
    ``reset``s it on each inbound frame; asyncio has no ``pin`` + ``reset``, so
    the liveness ``deadline_task`` is created once before the loop, kept running
    across iterations (excluded from the pending-cancel sweep), and cancelled +
    rebuilt only when an inbound frame arrives. Naively rebuilding it every
    iteration would let the 5s ``clock_probe`` arm preempt it on every cycle and
    the deadline would never fire.
    """
    clock_probe = _Interval(CLOCK_PROBE_INTERVAL)
    # Consume the immediate first tick (tokio::time::interval fires once on
    # construction); the first real probe lands one CLOCK_PROBE_INTERVAL later.
    await clock_probe.tick()
    deadline_task: asyncio.Future[None] = asyncio.ensure_future(
        asyncio.sleep(liveness_deadline)
    )
    try:
        while True:
            stop_task = asyncio.ensure_future(stop_rx.recv())
            reconnect_task = asyncio.ensure_future(reconnect_rx.recv())
            clock_task = asyncio.ensure_future(clock_probe.tick())
            stream_task = asyncio.ensure_future(_next_inbound(stream))
            done, pending = await asyncio.wait(
                (
                    stop_task,
                    reconnect_task,
                    stream_task,
                    clock_task,
                    deadline_task,
                ),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel every pending arm except the liveness deadline: Rust pins
            # it outside the select! and only rearms it on an inbound frame, so
            # it must keep counting down across iterations.
            reap = [task for task in pending if task is not deadline_task]
            for task in reap:
                task.cancel()
            if reap:
                await asyncio.gather(*reap, return_exceptions=True)

            if stop_task in done:
                return Stop()
            if reconnect_task in done:
                _LOGGER.info("forced reconnect requested; dropping current socket")
                return SocketClosed(Forced())
            if stream_task in done:
                inbound = stream_task.result()
                if isinstance(inbound, WsFrameReceived):
                    message = inbound.message
                    if not isinstance(message, WsClose):
                        inner.health.record_inbound()
                    # Rearm the liveness deadline (Rust
                    # ``deadline.as_mut().reset(now + liveness_deadline)``): the
                    # prior task is either done (it lost the race) or pending-
                    # kept; cancel + reap it before starting a fresh window.
                    if not deadline_task.done():
                        deadline_task.cancel()
                        await asyncio.gather(deadline_task, return_exceptions=True)
                    deadline_task = asyncio.ensure_future(
                        asyncio.sleep(liveness_deadline)
                    )
                    if isinstance(message, WsText):
                        pong = route_or_pong(inner, message.text)
                        if pong is not None:
                            try:
                                inner.outbound_tx.try_send(pong)
                            except (asyncio.QueueFull, _MpscClosed):
                                _LOGGER.warning(
                                    "heartbeat pong dropped: outbound channel "
                                    "full or closed"
                                )
                        continue
                    if isinstance(message, (WsPing, WsPong, WsRaw)):
                        continue
                    if isinstance(message, WsBinary):
                        _LOGGER.warning("server sent a binary frame; ignoring")
                        continue
                    # WsClose: classify the close code and exit.
                    return exit_for_close_code(message.code)
                if isinstance(inbound, WsReadError):
                    return SocketClosed(classify_stream_end(inner, inbound.detail))
                # WsStreamEnd: clean EOF with no read-error detail.
                return SocketClosed(classify_stream_end(inner, None))
            if clock_task in done:
                inner.health.refresh_clock()
                continue
            # deadline_task in done: no inbound frame within the liveness window.
            _LOGGER.warning(
                "no inbound frame within the liveness deadline (%ss); "
                "declaring the socket dead and reconnecting",
                liveness_deadline,
            )
            return SocketClosed(LivenessDeadline())
    finally:
        # On any exit, reap the liveness deadline task (it may still be pending
        # if the loop returned via the stop / reconnect / stream / clock arm).
        if not deadline_task.done():
            deadline_task.cancel()
            await asyncio.gather(deadline_task, return_exceptions=True)


class ReconnectFn(Protocol):
    """Reconnect + session-replay strategy, dependency-injected into the actor.

    The real ``reconnect_and_replay`` (open a fresh socket, re-run the
    handshake, replay parked sessions) is a network-bound leaf (R162+); the
    actor takes this :class:`Protocol` so its entire orchestration -- the exit
    dispatch, the outage bookkeeping, the biased backoff / reconnect select,
    the writer Pause/Resume handshake, and the fatal-vs-transient retry state
    machine -- is unit-testable with a scripted ``reconnect_fn`` and no live
    transport, mirroring :func:`run_reader_phase`'s "generic over the inbound
    stream" technique. ``Ok`` yields ``(sink, stream)``; any
    :class:`HandshakeAuthFailed` is fatal (pool eviction), every other error
    is transient (retry after backoff).
    """

    async def __call__(
        self,
        inner: HubConnectionInner,
        url: str,
        attempt: int,
        outage: OutageInfo,
        backoff_total: float,
    ) -> tuple[Any, AsyncIterator[WsInbound]]:
        ...


async def _send_writer_ctl(
    writer_ctl_tx: _Sink[WriterControl[Any]], ctl: WriterControl[Any]
) -> bool:
    """Send a ``WriterControl`` signal; return False if the writer channel closed.

    Mirrors Rust ``writer_ctl_tx.send(ctl).await.is_err()``: :meth:`_Sink.send`
    is async and raises :class:`_MpscClosed` when the writer task has dropped
    its receiver (writer gone). ``True`` on a clean hand-off.
    """
    try:
        await writer_ctl_tx.send(ctl)
    except _MpscClosed:
        return False
    return True


def _forget_pool_entry(inner: HubConnectionInner) -> None:
    """Evict this connection from the pool on a fatal (auth) failure.

    Mirrors Rust ``forget_pool_entry(&inner)``: the inner carries an
    ``on_fatal`` weakref to its pool; resolve it and remove the entry whose
    actor identity matches this inner (Rust ``Arc::as_ptr as usize`` ->
    :func:`id`).
    """
    if inner.on_fatal is None:
        return
    pool = inner.on_fatal()
    if pool is None:
        return
    own_id = id(inner)
    pool.forget_if(inner.key, lambda conn: conn.actor_id() == own_id)


async def run_reader_actor(
    inner: HubConnectionInner,
    stream: AsyncIterator[WsInbound],
    stop_rx: _SinkRx[None],
    reconnect_rx: _SinkRx[None],
    writer_ctl_tx: _Sink[WriterControl[Any]],
    writer_stop_tx: _Sink[None],
    writer_handle: asyncio.Task[Any],
    url: str,
    liveness_deadline: float,
    reconnect_fn: ReconnectFn,
) -> None:
    """Reader actor orchestration loop (Rust ``run_reader_actor``, 1088-1242).

    Consumes :func:`run_reader_phase`'s :class:`ConnectedExit` and drives the
    reconnect lifecycle. Three exit branches, audited in declaration order:

    * :class:`Stop` -> break to cleanup (no reconnect, no disconnect drain).
    * :class:`TerminalClose` -> log + :func:`fire_on_disconnect` + drain waiters
      as :class:`Closed` + drain progress, then break (the peer closed
      permanently; reconnecting would loop).
    * :class:`SocketClosed` -> build an :class:`OutageInfo` snapshot from the
      health tracker + detection latency, log + :func:`fire_on_disconnect` +
      ``WriterControl.Pause`` (break on a closed channel) + drain waiters as
      :class:`NetworkError` + drain progress, then enter the inner reconnect
      loop.

    The inner reconnect loop biases stop over backoff-sleep and over the
    reconnect attempt (Rust ``tokio::select!`` declaration order), so a stop
    signal preempts an in-flight reconnect. Each attempt is bounded by
    :func:`reconnect_attempt_budget`. Outcomes: ``Ok((sink, stream))`` ->
    metrics + health reset + writer-error take + :func:`drain_reconnect_signals`
    + ``WriterControl.Resume`` (break on closed) + resume metric + break back
    to the steady-state reader; :class:`HandshakeAuthFailed` -> metric +
    drain-as-:class:`AuthError` + :func:`_forget_pool_entry` + fatal stop;
    anything else (transport error, ``TimeoutError`` from the per-attempt
    budget) -> metric + warn + retry.

    Cleanup (unconditional on any actor exit) drains waiters as
    :class:`NetworkError`, drains progress, signals the writer to stop, closes
    the writer-control + stop channels, awaits the writer task (warn on error),
    and sets the shutdown event so :meth:`HubConnection.await_shutdown` resolves.

    tokio -> asyncio: Rust labeled ``break 'actor`` / ``break 'reconnect`` ->
    a ``stop_actor`` flag re-checked at each loop break site; Rust
    ``mpsc::Sender::send(..).await.is_err()`` -> :func:`_send_writer_ctl`; Rust
    ``timeout(budget, reconnect(..))`` -> :func:`asyncio.wait_for` with
    ``TimeoutError`` flowing into the transient-retry branch.
    """
    attempt = 0
    connected_at = time.monotonic()
    backoff_total = 0.0
    stop_actor = False
    try:
        while True:  # 'actor
            exit_ = await run_reader_phase(
                inner, stream, stop_rx, reconnect_rx, liveness_deadline
            )
            if isinstance(exit_, Stop):
                break
            if isinstance(exit_, TerminalClose):
                _LOGGER.info(
                    "connection closed by peer (terminal close code %s)", exit_.code
                )
                fire_on_disconnect(inner)
                inner.demux.drain_waiters_with(
                    lambda: Closed("connection closed by peer")
                )
                inner.demux.drain_progress()
                break
            # SocketClosed(cause): transient outage -> attempt reconnect.
            detected_at = time.monotonic()
            health = inner.health.snapshot()
            outage = OutageInfo(
                cause=exit_.cause,
                prev_connection_id=inner.connection_id.get(),
                prev_connection_duration_ms=max(
                    0, int((detected_at - connected_at) * 1000)
                ),
                last_inbound_mono=health.last_inbound_mono,
                detect_ms=max(
                    0, int((detected_at - health.last_inbound_mono) * 1000)
                ),
                since_last_probe_monotonic_ms=health.since_last_probe_monotonic_ms,
                since_last_probe_wall_ms=health.since_last_probe_wall_ms,
                clock_jump_ms=health.clock_jump_ms,
            )
            _LOGGER.warning(
                "connection lost (%s); pausing writer and reconnecting to %s",
                exit_.cause.label(),
                url,
            )
            fire_on_disconnect(inner)
            if not await _send_writer_ctl(writer_ctl_tx, Pause()):
                break
            # Snapshot the loop variable's label into a plain local first, then
            # bind via a default arg: drain_waiters_with invokes the factory
            # synchronously, but B023 cannot see that, so we freeze via a
            # default; the default is a plain local (not a call), satisfying B008.
            cause_label = exit_.cause.label()
            inner.demux.drain_waiters_with(
                lambda cl=cause_label: NetworkError(f"connection lost: {cl}")
            )
            inner.demux.drain_progress()

            while True:  # 'reconnect
                attempt += 1
                backoff = backoff_for(attempt, inner.reconnect_backoff)
                # Biased stop > backoff-sleep (Rust select! declaration order).
                stop_task = asyncio.ensure_future(stop_rx.recv())
                sleep_task = asyncio.ensure_future(asyncio.sleep(backoff))
                done, pending = await asyncio.wait(
                    (stop_task, sleep_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done:
                    stop_actor = True
                    break
                backoff_total += backoff
                reconnect_start = time.monotonic()
                attempt_budget = reconnect_attempt_budget(liveness_deadline)
                # Biased stop > reconnect attempt (budget-bounded).
                reconnect_task = asyncio.ensure_future(
                    asyncio.wait_for(
                        reconnect_fn(inner, url, attempt, outage, backoff_total),
                        timeout=attempt_budget,
                    )
                )
                stop_task = asyncio.ensure_future(stop_rx.recv())
                done, pending = await asyncio.wait(
                    (stop_task, reconnect_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done:
                    stop_actor = True
                    break
                exc = reconnect_task.exception()
                if exc is None:
                    new_sink, stream = reconnect_task.result()
                    reconnect_succeeded()
                    reconnect_duration_observe(time.monotonic() - reconnect_start)
                    inner.health.reset()
                    inner.writer_error.take()
                    drain_reconnect_signals(reconnect_rx)
                    if not await _send_writer_ctl(writer_ctl_tx, Resume(new_sink)):
                        stop_actor = True
                        break
                    reconnect_writer_resume()
                    break  # break 'reconnect -> re-enter steady-state reader
                if isinstance(exc, HandshakeAuthFailed):
                    reconnect_failed("handshake_auth")
                    # Default-arg bind (see the SocketClosed drain site above).
                    inner.demux.drain_waiters_with(
                        lambda status=exc.status: AuthError(f"handshake rejected: status {status}")
                    )
                    inner.demux.drain_progress()
                    _forget_pool_entry(inner)
                    stop_actor = True
                    break
                reconnect_failed("transport")
                _LOGGER.warning(
                    "reconnect attempt %d to %s failed: %r", attempt, url, exc
                )
                # Continue 'reconnect -> retry after the next backoff.
            if stop_actor:
                break
    finally:
        inner.demux.drain_waiters_with(
            lambda: NetworkError("connection actor exited")
        )
        inner.demux.drain_progress()
        try:
            writer_stop_tx.try_send(None)
        except (_MpscClosed, asyncio.QueueFull):
            pass
        writer_ctl_tx.close()
        stop_rx.close_channel()
        try:
            await writer_handle
        except Exception:
            _LOGGER.warning("writer task exited with an error during teardown")
        inner.shutdown.set()


# ===========================================================================
# Network primitives (R162, SDK leaf 19a).
# ===========================================================================
# Forward-port of connection.rs:877-931 (``open_socket``). ``host_is_loopback``
# (861-869) was already landed in R155 (leaf 18f) and is reused as-is here --
# this leaf is the socket-opening orchestrator that builds on it. Every connect
# / reconnect opens a fresh socket via :func:`open_socket`, then runs the
# handshake (``run_handshake``, R163+) on top. The raw ``connect_async`` call is
# dependency-injected via the :class:`WebSocketDial` Protocol so this leaf ships
# with zero live-transport dependency -- the same "generic over the network,
# unit-test the orchestration" technique R160/R161 used for the reader actor
# (``AsyncIterator[WsInbound]``) and the reconnect strategy (``ReconnectFn``). A
# real dialer (a ``websockets`` / ``aiohttp`` adapter) is a future consumer-leaf
# concern; ``pyproject.toml`` intentionally ships no WS client library.
# ``_resolve_role_query`` (connection.rs:899-913) is the third pure-logic helper
# this leaf introduces -- it normalises / validates the ``role`` query parameter
# before the dialer is called.


class WebSocketDial(Protocol):
    """Raw WebSocket client connect, dependency-injected (R162).

    Mirrors ``tokio_tungstenite::connect_async``: given the (role-normalised,
    auth + traceparent-headed) URL and header bundle, open a fresh ``ws://`` /
    ``wss://`` socket and return the library's stream object. A real adapter
    wires ``websockets.connect`` / ``aiohttp``; tests wire a scripted fake. The
    dialer signals an HTTP-level auth rejection by raising an exception
    carrying a ``status`` attribute (401/403) so
    :meth:`ClientError.from_handshake_error` classifies it as
    :class:`HandshakeAuthFailed` before it collapses to a transport
    :class:`NetworkError`; any other exception is a transient transport failure.
    The return type is ``Any`` (library-dependent), matching the
    ``ReconnectFn`` / ``WsInbound`` stream-typing convention.
    """

    async def __call__(self, url: str, headers: list[tuple[str, str]]) -> Any:
        ...


def _resolve_role_query(url: str, kind: ConnectionKind) -> str:
    """Return ``url`` with the ``role`` query parameter set to ``kind``'s wire value.

    Mirrors connection.rs:899-913. If the URL already carries ``role``, it must
    agree with ``kind`` (else :class:`InvalidConfig` -- a harness URL cannot be
    opened as a tool-server and vice versa); otherwise ``role=<kind.value>`` is
    appended. Other query parameters (and the fragment) are preserved. The
    ``role`` value is the :class:`ConnectionKind` StrEnum's string value
    (``Harness`` -> ``"harness"``, ``ToolServer`` -> ``"tool_server"``).
    ``parse_qsl`` (not ``parse_qs``) with ``keep_blank_values=True`` is used so
    an empty ``role=`` is observed verbatim, matching Rust's
    ``url::query_pairs`` which yields blank values. Python's default
    ``keep_blank_values=False`` would drop ``role=`` exactly like ``parse_qs``,
    silently masking a misconfigured URL as a fresh append.
    """
    expected_role = kind.value
    parsed = urlparse(url)
    pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    existing = next((v for k, v in pairs if k == "role"), None)
    if existing is not None and existing != expected_role:
        raise InvalidConfig(
            f"URL query parameter role={existing} conflicts with "
            f"ConnectionKind.{kind.name} (expected role={expected_role})"
        )
    if existing == expected_role:
        return url
    # Append role=<expected>, preserving any pre-existing query pairs (order kept).
    pairs.append(("role", expected_role))
    new_query = urlencode(pairs)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


async def open_socket(
    url: str,
    credential: AuthCredential,
    kind: ConnectionKind,
    alpha_test_key: str | None,
    allow_insecure_ws: bool,
    dial: WebSocketDial,
) -> Any:
    """Open a fresh ``ws://`` / ``wss://`` socket (no handshake yet).

    Mirrors ``open_socket`` (connection.rs:877). Three pure-logic gates run
    before any network call:

    1. **Plaintext-safety gate** -- sending the credential over ``ws://`` to a
       non-loopback host would cross the network in cleartext, so it is refused
       unless ``allow_insecure_ws`` explicitly opts in. Loopback
       (``127.0.0.1`` / ``::1`` / ``localhost``) is the development /
       local-proxy exception (checked via :func:`host_is_loopback`).
    2. **Role query normalisation** -- the ``role`` query parameter is set to
       ``kind``'s wire value (or validated if already present) via
       :func:`_resolve_role_query`.
    3. **Header injection** -- the credential's upgrade headers
       (:meth:`AuthCredential.upgrade_headers`) plus the active W3C traceparent
       (:func:`attach_trace_to_http_request`, R131) are merged into one bundle.

    Then the dialer is invoked. A 401/403 on the HTTP upgrade is raised as
    :class:`HandshakeAuthFailed` (non-retryable -- replaying the same credential
    is rejected identically); any other dialer failure is raised as a transport
    :class:`NetworkError`.

    ``alpha_test_key`` is accepted for API parity with the Rust signature but
    intentionally unused (Rust: ``let _ = alpha_test_key;``) -- alpha-test
    routing is server-side.
    """
    scheme = urlparse(url).scheme
    is_plaintext_remote = scheme != "wss" and not host_is_loopback(url)
    if is_plaintext_remote and not allow_insecure_ws:
        raise InsecureScheme(url)
    if is_plaintext_remote:
        _LOGGER.warning(
            "opening server connection over plaintext ws:// "
            "(allow_insecure_ws=true); bearer crosses the network in cleartext (host=%s)",
            urlparse(url).hostname or "",
        )

    connect_url = _resolve_role_query(url, kind)

    # Credential upgrade headers + active traceparent, merged in one mutable
    # mapping (attach_trace_to_http_request writes into it), then materialised
    # to the (name, value) pair list the dialer contract takes.
    headers: dict[str, str] = {}
    for name, value in credential.upgrade_headers():
        headers[name] = value
    attach_trace_to_http_request(headers)

    _ = alpha_test_key  # API parity; unused (alpha-test routing is server-side).

    try:
        ws = await dial(connect_url, list(headers.items()))
    except Exception as exc:
        raise ClientError.from_handshake_error(exc) from exc
    return ws
