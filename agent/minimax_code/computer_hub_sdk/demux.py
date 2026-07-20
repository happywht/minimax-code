"""Inbound frame demultiplexer (R149).

Fusion of grok-build's ``xai-computer-hub-sdk/src/demux.rs`` (973 lines) --
the SDK crate's 17th leaf (after R133 error / R134 handshake / R135 refcount /
R136 donate_pump / R137 trace_donate / R138 connection_borrow / R139 auth /
R140 observability / R141 cancel / R142 admission / R143 pool / R144
notification / R145 oidc_provider / R146 metric_donate / R147 metrics / R148
log_donate).

The demux is the inbound side of the SDK connection: a single ``route(frame)``
classifies each parsed JSON value coming off the WebSocket and hands it to
exactly one consumer without ever blocking the read loop. Four buckets:

1. **JSON-RPC responses** (frames carrying ``result`` / ``error``) are
   correlated by ``id`` to a parked response waiter (a tool.call's reply, or a
   turn hook's ack). A hit resolves the oneshot; a miss is dropped (late /
   duplicate / unsolicited).
2. **``tool_call_progress`` notifications** are correlated by
   ``params.tool_call_id`` to the call's progress stream.
3. **JSON-RPC requests / notifications scoped to a session** (frames carrying
   ``session_id``) land in that session's inbox (an mpsc the harness drains).
4. **Connection-level notifications** (frames carrying ``method`` but no
   ``session_id``) are broadcast to every subscriber.

The cardinal rule is **non-blocking routing**: every inbox / progress handoff
uses ``put_nowait``. A full inbox returns ``InboxFull`` (and, for a Request,
synthesizes the shared ``-32016`` "tool_busy" rejection onto the outbound
sink); a dropped receiver returns ``SessionDropped`` / ``ProgressDropped`` and
prunes the stale binding. ``route`` never awaits.

What migrates vs what does NOT (YAGNI boundary)
-----------------------------------------------

MIGRATED (transport-agnostic routing + correlation):

* :class:`Demux` -- the stateful router: 4 maps (sessions / response waiters /
  call-session index / progress waiters) + 1 broadcast + optional outbound
  sink. 11 management methods (register / unregister / take / fail / drain)
  + the synchronous :meth:`Demux.route` classifier + 3 private sub-routers.
* :class:`RouteOutcome` -- the 11 terminal outcomes (1:1 with the Rust enum).
* :class:`InboundFrame` -- a session-scoped frame tagged request vs
  notification (the Rust ``enum InboundFrame { Request(Value),
  Notification(Value) }`` collapsed to a small ``__slots__`` class because
  Python carries the discriminator explicitly at construction).
* :func:`mpsc_channel` + :class:`_Sink` / :class:`_SinkRx` / :class:`_Channel`
  -- a bounded mpsc pair (the ``tokio::sync::mpsc::channel`` equivalent).
  ``try_send`` raises :class:`_MpscClosed` (receiver gone) or
  :class:`asyncio.QueueFull` (capacity); it never blocks.
* :class:`_NotificationBroadcast` -- the minimal ``broadcast::channel(64)``
  substitute (a list of subscriber queues; ``send`` does ``put_nowait`` to
  each, QueueFull silently drops the laggard).
* :func:`_fulfill` -- the oneshot delivery helper (``oneshot::Sender::send``
  returning ``Result`` -> bool: ``True`` accepted, ``False`` already
  resolved / cancelled).

NOT MIGRATED (framework glue with no Python equivalent):

* ``tokio::sync::mpsc`` / ``oneshot`` / ``broadcast`` themselves -- Python has
  ``asyncio.Queue`` but no ``close`` semantics and no broadcast primitive; the
  three small classes above are the faithful substitutes, scoped to what the
  demux actually does (the harness / connection leaves, later, will reuse
  :func:`mpsc_channel` for their own inboxes).
* ``dashmap::DashMap`` entry API (``Occupied`` / ``Vacant``) -- asyncio is
  single-threaded under the GIL, so a plain ``dict`` with ``key in map``
  checks is the atomic-equivalent. No shard locking.
* The reconnect-path callers (``drain_waiters_with`` + ``drain_progress`` are
  wired by ``connection.rs`` / ``harness.rs`` in later leaves); the methods
  are ported now because the demux owns the state they drain.

Python-specific adaptations (no behavior change)
------------------------------------------------

* ``route(&self, frame: Value) -> RouteOutcome`` (sync) -> a plain method,
  not a coroutine. Every queue handoff is ``put_nowait``; ``route`` is safe to
  call from the synchronous frame-parse path.
* ``mpsc::Sender::try_send`` (``Result<(), TrySendError<T>>``) -> ``_Sink``
  raising :class:`_MpscClosed` / :class:`asyncio.QueueFull`; the caller's
  ``match`` becomes ``try/except``.
* ``mpsc::Receiver::recv`` -> ``await _SinkRx.recv()`` returning ``T | None``
  (``None`` once closed AND drained). ``drop(rx)`` -> ``_SinkRx.close_channel``
  (sets the ``closed`` flag so the sender's next ``try_send`` rejects).
* ``oneshot::Sender::send`` -> :func:`_fulfill` against an
  :class:`asyncio.Future`; ``is_ok`` -> ``True``, receiver-dropped -> ``False``
  (future already done / cancelled).
* ``broadcast::Sender::send`` (``let _ = ...`` fire-and-forget) ->
  :class:`_NotificationBroadcast.send` iterating ``put_nowait``; a lagging
  subscriber's QueueFull is silently dropped (Rust broadcast lag).
* ``serde_json::to_string`` (compact) for the malformed-id echo ->
  ``json.dumps(value, separators=(",", ":"))`` -- matches Rust's compact JSON
  byte-for-byte (``{"nested":1}``, no spaces).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from enum import Enum
from typing import Generic, TypeVar

from minimax_code.computer_hub_sdk.admission import overloaded_response
from minimax_code.computer_hub_sdk.error import ClientError, SerdeError
from minimax_code.computer_hub_sdk.metrics import (
    demux_inbox_depth_set,
    inbox_full_notification_dropped,
    inbox_full_reject_send_failed,
    inbox_full_request_rejected,
    progress_frame_forwarded,
)
from minimax_code.tool_protocol.envelope import (
    JsonRpcId,
    JsonRpcIdString,
    JsonRpcResponse,
    jsonrpc_id_from_wire,
)
from minimax_code.tool_protocol.frames import (
    ToolCallProgressFrame,
    tool_call_progress_frame_from_wire,
)
from minimax_code.tool_protocol.ids import RequestId, SessionId, ToolCallId

__all__ = ["Demux", "InboundFrame", "RouteOutcome", "mpsc_channel"]

_LOG = logging.getLogger(__name__)
T = TypeVar("T")

#: Wire method name for an inbound tool-call progress notification (R149).
_METHOD_TOOL_CALL_PROGRESS = "tool_call_progress"
#: Subscriber queue depth for the connection-level notification broadcast
#: (Rust ``broadcast::channel(64)``).
_NOTIFICATION_CAPACITY = 64


# ---------------------------------------------------------------------------
# mpsc channel pair -- the bounded buffer + a closed flag.
#
# Python's asyncio.Queue has no close; the ``closed`` flag is the
# ``drop(rx)`` equivalent, letting ``try_send`` reject without blocking once
# the receiver is gone (or the demux has drained the channel).
# ---------------------------------------------------------------------------
class _MpscClosed(Exception):
    """The receiving half of an mpsc channel is gone (R149).

    Mirrors Rust ``tokio::sync::mpsc::TrySendError::Closed``: the receiver was
    dropped (or the channel was explicitly closed via :meth:`_Sink.close` /
    :meth:`_SinkRx.close_channel`), so ``try_send`` rejects without blocking.
    """


class _Channel(Generic[T]):
    """Shared state of a bounded mpsc channel pair (R149).

    Combines an :class:`asyncio.Queue` (the bounded buffer) with a ``closed``
    flag. Both the sender (:class:`_Sink`) and the receiver (:class:`_SinkRx`)
    hold a reference to the same instance, so closing from either side is
    visible to the other.
    """

    __slots__ = ("buffer", "capacity", "closed")

    def __init__(self, capacity: int) -> None:
        self.buffer: asyncio.Queue[T] = asyncio.Queue(maxsize=capacity)
        self.capacity = capacity
        self.closed = False


class _Sink(Generic[T]):
    """Sending half of a bounded mpsc channel (R149).

    ``try_send`` mirrors Rust ``mpsc::Sender::try_send``: closed (receiver
    gone) -> :class:`_MpscClosed`; full -> :class:`asyncio.QueueFull`. It never
    blocks, so it is safe to call from the synchronous ``route`` path.
    """

    __slots__ = ("_channel",)

    def __init__(self, channel: _Channel[T]) -> None:
        self._channel = channel

    def try_send(self, value: T) -> None:
        if self._channel.closed:
            raise _MpscClosed()
        # put_nowait raises asyncio.QueueFull when at capacity.
        self._channel.buffer.put_nowait(value)

    async def send(self, value: T) -> None:
        """Blocking send: await a free slot (Rust ``mpsc::Sender::send``, R152).

        The connection's outbound backpressure path needs the async ``send`` that
        Rust's ``tokio::sync::mpsc::Sender::send`` provides: when the bounded
        buffer is full, ``send`` parks until a slot frees rather than rejecting
        like :meth:`try_send`. Mirrors the Rust contract -- a close observed
        before the await raises :class:`_MpscClosed` (receiver gone).

        Edge case: ``asyncio.Queue.put`` does not observe the cooperative
        ``closed`` flag mid-await, so a close arriving *during* the wait still
        lets the value land in the buffer (lost on the receiver side). The
        outbound caller catches the closed case up front via :meth:`try_send`
        and only falls through to ``send`` on :class:`asyncio.QueueFull`, so the
        already-full buffer is the sole mid-send state.
        """
        if self._channel.closed:
            raise _MpscClosed()
        await self._channel.buffer.put(value)

    def close(self) -> None:
        """Mark the channel closed (Rust drop-all-senders equivalent, R149).

        Used by :meth:`Demux.drain_progress`: dropping the parked senders
        closes each channel so the receiver's ``recv`` returns ``None`` once
        drained. Subsequent ``try_send`` raises :class:`_MpscClosed`.
        """
        self._channel.closed = True


class _SinkRx(Generic[T]):
    """Receiving half of a bounded mpsc channel (R149).

    ``recv`` mirrors Rust ``mpsc::Receiver::recv``: returns the next item, or
    ``None`` once the channel is closed AND drained (all senders dropped).
    ``close_channel`` is the ``drop(rx)`` equivalent (the demux uses it to
    model a dropped receiver in tests and in the harness shutdown path).
    """

    __slots__ = ("_channel",)

    def __init__(self, channel: _Channel[T]) -> None:
        self._channel = channel

    def close_channel(self) -> None:
        """Drop the receiver side: mark closed so senders reject (R149)."""
        self._channel.closed = True

    async def recv(self) -> T | None:
        # Fast path / drain: closed and empty -> None (sender dropped).
        if self._channel.closed and self._channel.buffer.empty():
            return None
        return await self._channel.buffer.get()

    def try_recv(self) -> T | None:
        """Non-blocking receive: next item, or ``None`` if empty (R149)."""
        if self._channel.buffer.empty():
            return None
        return self._channel.buffer.get_nowait()


def mpsc_channel(capacity: int) -> tuple[_Sink[T], _SinkRx[T]]:
    """Create a bounded mpsc channel pair (R149).

    Mirrors Rust ``tokio::sync::mpsc::channel(capacity)``: returns
    ``(sender, receiver)`` sharing a bounded buffer of ``capacity`` slots.
    """
    channel: _Channel[T] = _Channel(capacity)
    return _Sink(channel), _SinkRx(channel)


# ---------------------------------------------------------------------------
# Inbound frame + route outcome.
# ---------------------------------------------------------------------------
class InboundFrame:
    """A session-scoped inbound frame, request vs notification (R149).

    Rust ``enum InboundFrame { Request(Value), Notification(Value) }``. The
    demux does not carry the discriminator on the wire; :meth:`Demux.route`
    builds it from ``"id" in frame`` (a frame with an ``id`` is a Request the
    harness must answer; one without is a fire-and-forget Notification).
    Collapsed to a small class with ``__slots__`` carrying the discriminator
    flag + the raw frame dict.
    """

    __slots__ = ("is_request", "value")

    def __init__(self, is_request: bool, value: dict[str, object]) -> None:
        self.is_request = is_request
        self.value = value

    def __repr__(self) -> str:
        kind = "Request" if self.is_request else "Notification"
        return f"InboundFrame::{kind}({self.value!r})"


class RouteOutcome(Enum):
    """The 11 terminal outcomes of inbound frame routing (R149).

    Each variant maps 1:1 to a Rust ``RouteOutcome`` arm. The ``route``
    classifier returns exactly one per frame; callers (the harness read loop)
    switch on it for metrics / tracing without re-deriving the classification.
    """

    Response = "response"
    Session = "session"
    Progress = "progress"
    UnknownSession = "unknown_session"
    UnknownProgress = "unknown_progress"
    Notification = "notification"
    Unrouted = "unrouted"
    InboxFull = "inbox_full"
    SessionDropped = "session_dropped"
    ProgressFull = "progress_full"
    ProgressDropped = "progress_dropped"


# ---------------------------------------------------------------------------
# Connection-level notification broadcast (Rust broadcast::channel(64)).
# ---------------------------------------------------------------------------
class _NotificationBroadcast:
    """Minimal broadcast channel for connection-level notifications (R149).

    Rust ``tokio::sync::broadcast::channel(64)`` with a single fire-and-forget
    ``send`` -- the demux ``route`` path ignores send errors (``let _ = ...``).
    Python has no broadcast primitive, so this is the minimal substitute: a
    list of subscriber queues, ``send`` does ``put_nowait`` to each, and a
    lagging subscriber's :class:`asyncio.QueueFull` is silently dropped (the
    Rust broadcast "lag" semantics -- a slow subscriber misses messages, the
    fast ones are unaffected). No metrics, no exception.
    """

    __slots__ = ("_capacity", "_subscribers")

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._subscribers: list[asyncio.Queue[dict[str, object]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        """Register a new subscriber; return its bounded queue (R149)."""
        q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=self._capacity)
        self._subscribers.append(q)
        return q

    def send(self, frame: dict[str, object]) -> None:
        """Fan-out ``frame`` to every subscriber; lagging ones drop silently."""
        for q in self._subscribers:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass


def _fulfill(fut: asyncio.Future, result: object) -> bool:
    """Deliver a result to a oneshot waiter future (R149).

    Mirrors Rust ``oneshot::Sender::send`` returning ``Result<(), ...>``:
    ``True`` if the future accepted the result, ``False`` if it was already
    resolved or cancelled (receiver dropped, so the send is a no-op). A
    :class:`BaseException` result (a :class:`ClientError` subclass) completes
    via ``set_exception``; anything else (a :class:`JsonRpcResponse`) completes
    via ``set_result``. Single-threaded asyncio + synchronous ``route`` means
    the done-check-then-set pair is atomic.
    """
    if fut.done():
        return False
    if isinstance(result, BaseException):
        fut.set_exception(result)
    else:
        fut.set_result(result)
    return True


# ---------------------------------------------------------------------------
# Demux -- the inbound frame router.
# ---------------------------------------------------------------------------
class Demux:
    """Inbound frame demultiplexer (R149).

    State (4 maps + 1 broadcast + 1 optional outbound sink):

    * ``sessions``: ``SessionId -> mpsc inbox sender`` -- one per live session,
      drained by the harness. ``route`` pushes :class:`InboundFrame` here.
    * ``waiters``: ``RequestId -> oneshot future`` -- every parked response
      waiter (tool.call replies AND turn-hook acks).
    * ``call_sessions``: ``RequestId -> SessionId`` -- indexes ONLY the
      ``tool.call`` response waiters, so :meth:`fail_calls_for_session` can
      short-circuit a dead session's in-flight calls without touching turn
      hooks.
    * ``progress``: ``ToolCallId -> mpsc progress sender`` -- one per in-flight
      tool.call, fed by ``tool_call_progress`` notifications.
    * ``notifications``: ``broadcast(64)`` -- connection-level notifications.
    * ``outbound``: optional ``mpsc<String>`` sink for synthesized rejections
      (the inbox-full ``-32016`` "tool_busy" response). ``None`` in unit
      contexts; ``route`` still reports ``InboxFull`` cleanly without it.
    """

    __slots__ = (
        "_sessions",
        "_waiters",
        "_call_sessions",
        "_progress",
        "_notifications",
        "_outbound",
    )

    def __init__(self, outbound: _Sink[str] | None = None) -> None:
        self._sessions: dict[SessionId, _Sink[InboundFrame]] = {}
        self._waiters: dict[RequestId, asyncio.Future] = {}
        self._call_sessions: dict[RequestId, SessionId] = {}
        self._progress: dict[ToolCallId, _Sink[ToolCallProgressFrame]] = {}
        self._notifications: _NotificationBroadcast = _NotificationBroadcast(
            _NOTIFICATION_CAPACITY
        )
        self._outbound: _Sink[str] | None = outbound

    @classmethod
    def new(cls) -> Demux:
        """Construct a bare demux with no outbound sink (R149)."""
        return cls()

    def with_outbound(self, outbound: _Sink[str]) -> Demux:
        """Builder: attach the outbound sink for synthesized rejections (R149)."""
        self._outbound = outbound
        return self

    # ------------------------------------------------------------------
    # Notification subscription.
    # ------------------------------------------------------------------
    def subscribe_notifications(self) -> asyncio.Queue[dict[str, object]]:
        """Subscribe to connection-level notifications (R149)."""
        return self._notifications.subscribe()

    # ------------------------------------------------------------------
    # Session inbox registration.
    # ------------------------------------------------------------------
    def register_session_inbox(
        self, session_id: SessionId, inbox: _Sink[InboundFrame]
    ) -> _Sink[InboundFrame] | None:
        """Bind ``inbox`` to ``session_id``; return the prior binding if any (R149).

        Mirrors Rust ``sessions.insert(session_id, inbox)`` returning the
        previous sender. A re-bind (rare) hands the caller the stale inbox so
        it can drain / close it.
        """
        previous = self._sessions.get(session_id)
        self._sessions[session_id] = inbox
        return previous

    def unregister_session_inbox(self, session_id: SessionId) -> _Sink[InboundFrame] | None:
        """Remove the ``session_id`` binding; return its inbox if any (R149)."""
        return self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # Response waiter registration.
    # ------------------------------------------------------------------
    def register_response_waiter(
        self, request_id: RequestId, waiter: asyncio.Future
    ) -> None:
        """Park a response waiter for ``request_id`` (R149).

        Used for non-call waiters (e.g. a turn hook's ack) -- NOT indexed in
        ``call_sessions``, so :meth:`fail_calls_for_session` leaves it parked.
        """
        self._waiters[request_id] = waiter

    def register_call_response_waiter(
        self, request_id: RequestId, session_id: SessionId, waiter: asyncio.Future
    ) -> None:
        """Park a ``tool.call`` response waiter, indexed by session (R149).

        The ``call_sessions`` index lets :meth:`fail_calls_for_session`
        short-circuit every in-flight call on a dead session.
        """
        self._call_sessions[request_id] = session_id
        self._waiters[request_id] = waiter

    def take_response_waiter(self, request_id: RequestId) -> asyncio.Future | None:
        """Remove and return the waiter for ``request_id`` (R149).

        Clears the ``call_sessions`` index entry unconditionally (a non-call
        waiter has none, so the pop is a harmless no-op). Returns ``None`` when
        no waiter is parked -- the caller treats this as "already resolved"
        (idempotent under short-circuit-then-late-response races).
        """
        self._call_sessions.pop(request_id, None)
        return self._waiters.pop(request_id, None)

    def fail_calls_for_session(
        self, session_id: SessionId, result_factory: Callable[[], ClientError]
    ) -> int:
        """Resolve every in-flight ``tool.call`` on ``session_id`` with an error (R149).

        Snapshots the matching request ids, takes each waiter, and fulfills it
        with ``result_factory()``. Non-call waiters (turn hooks) and other
        sessions' calls stay parked. Idempotent: a call already resolved (waiter
        taken) is not re-failed. Returns the count of waiters actually resolved.
        """
        request_ids = [
            rid for rid, sid in self._call_sessions.items() if sid == session_id
        ]
        resolved = 0
        for request_id in request_ids:
            waiter = self.take_response_waiter(request_id)
            if waiter is not None and _fulfill(waiter, result_factory()):
                resolved += 1
        return resolved

    def drain_waiters_with(self, result_factory: Callable[[], ClientError]) -> None:
        """Resolve EVERY parked waiter with ``result_factory()`` (R149).

        The reconnect-path drain: every response waiter (calls AND turn hooks)
        is resolved with the supplied error so callers do not block forever on
        a connection that is going away. Snapshots the keys first so the
        per-key remove is not fighting the iteration.
        """
        keys = list(self._waiters.keys())
        for key in keys:
            waiter = self._waiters.pop(key, None)
            if waiter is not None:
                self._call_sessions.pop(key, None)
                _fulfill(waiter, result_factory())

    # ------------------------------------------------------------------
    # Progress waiter registration.
    # ------------------------------------------------------------------
    def try_register_progress_waiter(
        self, tool_call_id: ToolCallId, progress: _Sink[ToolCallProgressFrame]
    ) -> tuple[bool, _Sink[ToolCallProgressFrame] | None]:
        """Register a progress waiter; reject collisions (R149).

        Mirrors Rust ``Entry::or_insert`` returning ``Result<(), Sender>``:
        ``(True, None)`` on insert, ``(False, progress)`` when ``tool_call_id``
        already has a live waiter (the rejected sender is handed back so the
        caller can drop it without disturbing the live channel). Single-threaded
        asyncio makes the check-then-set atomic.
        """
        if tool_call_id in self._progress:
            return (False, progress)
        self._progress[tool_call_id] = progress
        return (True, None)

    def unregister_progress_waiter(
        self, tool_call_id: ToolCallId
    ) -> _Sink[ToolCallProgressFrame] | None:
        """Remove the progress waiter for ``tool_call_id``; return it if any (R149)."""
        return self._progress.pop(tool_call_id, None)

    def drain_progress(self) -> None:
        """Drop every parked progress sender (R149).

        Used by the reconnect path after :meth:`drain_waiters_with`: each
        response waiter has been resolved with a :class:`NetworkError`, and the
        matching progress channel must close so the call's progress loop exits.
        Dropping the sender (``sender.close``) marks the channel closed; the
        receiver's ``recv`` returns ``None`` once drained.
        """
        keys = list(self._progress.keys())
        for key in keys:
            sender = self._progress.pop(key, None)
            if sender is not None:
                sender.close()

    # ------------------------------------------------------------------
    # route -- the synchronous main classifier.
    # ------------------------------------------------------------------
    def route(self, frame: dict[str, object]) -> RouteOutcome:
        """Classify and route a parsed JSON value (R149).

        Synchronous and non-blocking -- mirrors the Rust sync path. Order
        matters and is load-bearing:

        1. ``demux_inbox_depth_set`` records the live session count first.
        2. ``result`` OR ``error`` key -> JSON-RPC response (correlate by id).
        3. ``method == "tool_call_progress"`` -> progress notification.
        4. ``session_id`` present -> session-scoped request / notification.
        5. ``method`` present (no session) -> connection-level notification.
        6. otherwise -> :attr:`RouteOutcome.Unrouted`.

        Every queue handoff uses ``put_nowait``; a full inbox is rejected, a
        dropped receiver prunes its binding -- neither ever awaits.
        """
        demux_inbox_depth_set(len(self._sessions))
        if "result" in frame or "error" in frame:
            return self._route_response(frame)
        if frame.get("method") == _METHOD_TOOL_CALL_PROGRESS:
            return self._route_progress(frame)
        if "session_id" in frame:
            return self._route_session(frame)
        if "method" in frame:
            self._notifications.send(frame)
            return RouteOutcome.Notification
        return RouteOutcome.Unrouted

    # ------------------------------------------------------------------
    # Sub-routers.
    # ------------------------------------------------------------------
    def _route_progress(self, frame: dict[str, object]) -> RouteOutcome:
        # params must be a dict carrying a string tool_call_id.
        params = frame.get("params")
        if not isinstance(params, dict):
            return RouteOutcome.Unrouted
        call_id = params.get("tool_call_id")
        if not isinstance(call_id, str):
            return RouteOutcome.Unrouted
        try:
            tool_call_id = ToolCallId(call_id)
        except Exception:
            return RouteOutcome.Unrouted
        sender = self._progress.get(tool_call_id)
        if sender is None:
            return RouteOutcome.UnknownProgress
        # Decode the progress frame; a decode failure warns and drops (the
        # waiter stays parked for the next well-formed frame).
        try:
            progress_frame = tool_call_progress_frame_from_wire(params)
        except Exception:
            _LOG.warning(
                "failed to decode tool_call_progress params for %s", tool_call_id
            )
            return RouteOutcome.Unrouted
        progress_frame_forwarded()
        try:
            sender.try_send(progress_frame)
        except asyncio.QueueFull:
            _LOG.warning(
                "progress channel full for %s; dropping inbound progress frame",
                tool_call_id,
            )
            return RouteOutcome.ProgressFull
        except _MpscClosed:
            self._progress.pop(tool_call_id, None)
            return RouteOutcome.ProgressDropped
        return RouteOutcome.Progress

    def _route_response(self, frame: dict[str, object]) -> RouteOutcome:
        # id key absent -> Unrouted; null / bool / array / object id -> Unrouted.
        if "id" not in frame:
            return RouteOutcome.Unrouted
        request_id = self._match_request_id(frame["id"])
        if request_id is None:
            return RouteOutcome.Unrouted
        waiter = self.take_response_waiter(request_id)
        if waiter is None:
            # Late / duplicate / unsolicited response: no waiter parked.
            return RouteOutcome.Unrouted
        try:
            parsed: object = JsonRpcResponse.from_wire(frame)
        except Exception as err:
            # A response that fails JSON-RPC validation resolves the waiter
            # with a SerdeError so the caller surfaces the decode failure
            # rather than parking forever.
            parsed = SerdeError(str(err))
        _fulfill(waiter, parsed)
        return RouteOutcome.Response

    @staticmethod
    def _match_request_id(id_value: object) -> RequestId | None:
        """Map a wire ``id`` value to a :class:`RequestId`, or ``None`` (R149).

        Mirrors the Rust ``Value::String => RequestId::new(s)`` /
        ``Value::Number(n) => RequestId::new(n.to_string())`` / ``_ => None``
        match. bool is excluded before int (bool subclasses int in Python, but
        is ``Value::Bool`` in Rust, which hits the ``_`` arm). None / list /
        dict hit the ``_`` arm too.
        """
        if isinstance(id_value, bool):
            return None
        if isinstance(id_value, str):
            try:
                return RequestId(id_value)
            except Exception:
                return None
        if isinstance(id_value, int):
            try:
                return RequestId(str(id_value))
            except Exception:
                return None
        if isinstance(id_value, float):
            try:
                return RequestId(str(id_value))
            except Exception:
                return None
        return None

    def _route_session(self, frame: dict[str, object]) -> RouteOutcome:
        sid = frame.get("session_id")
        if not isinstance(sid, str):
            return RouteOutcome.Unrouted
        try:
            session_id = SessionId(sid)
        except Exception:
            return RouteOutcome.Unrouted
        inbox = self._sessions.get(session_id)
        if inbox is None:
            return RouteOutcome.UnknownSession
        kind = InboundFrame(is_request=("id" in frame), value=frame)
        try:
            inbox.try_send(kind)
        except asyncio.QueueFull:
            self._reject_inbox_full(session_id, kind)
            return RouteOutcome.InboxFull
        except _MpscClosed:
            _LOG.warning(
                "session inbox dropped; removing stale binding for %s", session_id
            )
            self._sessions.pop(session_id, None)
            return RouteOutcome.SessionDropped
        return RouteOutcome.Session

    def _reject_inbox_full(self, session_id: SessionId, frame: InboundFrame) -> None:
        """Handle a full session inbox (R149).

        A Notification is fire-and-forget: drop silently (meter
        ``inbox_full_notification_dropped``). A Request synthesizes the shared
        ``-32016`` "tool_busy" rejection onto the outbound sink so the caller
        learns the request was not delivered; if there is no outbound sink (a
        bare unit-context demux) the rejection is reported via the
        :attr:`RouteOutcome.InboxFull` return alone. A malformed ``id`` is
        echoed back as its raw JSON text (Rust ``Value::to_string`` compact).
        """
        if not frame.is_request:
            inbox_full_notification_dropped()
            return
        inbox_full_request_rejected()
        _LOG.warning(
            "session inbox full; rejecting request with tool_busy for %s",
            session_id,
        )
        out = self._outbound
        if out is None:
            return
        jid = self._echo_id(frame.value)
        response = overloaded_response(jid, session_id)
        try:
            text = json.dumps(response.to_wire(), separators=(",", ":"))
        except (TypeError, ValueError):
            return
        try:
            out.try_send(text)
        except (asyncio.QueueFull, _MpscClosed):
            inbox_full_reject_send_failed()

    @staticmethod
    def _echo_id(value: dict[str, object]) -> JsonRpcId:
        """Recover a :class:`JsonRpcId` from a request frame's ``id`` (R149).

        Mirrors the Rust ``raw_id.and_then(from_value::<JsonRpcId>.ok())
        .unwrap_or_else(|| JsonRpcId::new_string(raw_id.map(to_string)
        .unwrap_or_default()))``: a valid wire id (string / number) round-trips
        through :func:`jsonrpc_id_from_wire`; anything else (object / array /
        null / bool) is echoed back as its compact JSON text via
        :meth:`JsonRpcIdString.new_string` (``serde_json::to_string`` compact =
        ``json.dumps(..., separators=(",", ":"))``).
        """
        raw_present = "id" in value
        raw_id = value.get("id")
        try:
            return jsonrpc_id_from_wire(raw_id)
        except (ValueError, TypeError):
            text = json.dumps(raw_id, separators=(",", ":")) if raw_present else ""
            return JsonRpcIdString.new_string(text)
