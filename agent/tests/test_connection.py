"""Tests for ``minimax_code.computer_hub_sdk.connection`` (R151-R152).

Mirrors the ACTOR-STRUCTURE (R151) + NETWORK-OUTBOUND (R152) halves of
grok-build's ``xai-computer-hub-sdk/src/connection.rs`` lines 348-766 -- the
SDK crate's 18th leaf (18b: the ``HubConnection`` handle +
``HubConnectionInner`` shared state + ``ConnectionConfig`` + the pure-logic
accessors; 18c: the four network-outbound methods ``send_outbound`` /
``call_request`` / ``call_request_with_timeout`` / ``call_request_with_deadline``
-- lines 671-766). The remaining network-bound surface (``connect`` /
``serve``), the ``run_writer`` / ``run_reader_actor`` / ``open_socket`` /
``run_handshake`` spawn pipeline, and the ``WriterControl<S>`` state machine
(lines 806-1382) are later leaves (R153+); this leaf defines the type +
accessors + outbound path but not the socket / framing layer, so tests build
an inner directly with real :class:`_Sink` channels and exercise the accessors
without a socket (R152 fulfils the oneshot response-waiter via a recording
demux stand-in, matching Rust's ``oneshot::channel`` with an ``asyncio.Future``).

Rust test -> Python test mapping
--------------------------------

* Rust ``impl Debug for HubConnection`` (363-369) -> the ``__repr__`` test.
* Rust ``key`` / ``kind`` / ``actor_id`` / ``demux`` (596-636) -> the
  identity-accessor tests. ``actor_id`` uses :func:`id` (Rust
  ``Arc::as_ptr as usize``).
* Rust ``connection_id`` (612, async tokio::Mutex) -> the sync slot test (the
  Python ``threading.Lock`` read needs no await under the GIL).
* Rust ``supports`` (625, ``Option<bool>`` tri-state) -> the unknown / true /
  false tests across empty vs populated ``hello_capabilities``.
* Rust ``force_reconnect`` / ``request_shutdown`` (640, 657, ``let _ =``) ->
  the signal-sent + silent-on-closed tests (verified via channel buffer size,
  since the payload is ``None`` and indistinguishable from an empty drain).
* Rust ``await_shutdown`` (644, ``CancellationToken::cancelled().await``) ->
  the async test resolving once the shutdown ``Event`` is set.
* Rust ``track_session`` / ``untrack_session`` / ``bound_session_count`` (663,
  668, 798) -> the refcount tests (distinct-key count, multi-borrow, remove-
  at-zero).
* Rust ``try_send_outbound`` (771) -> the ok / full->``BackpressureError`` /
  closed->``NetworkError`` tests.
* Rust ``send_outbound`` (748-766, ``try_send`` ok / ``Full`` -> bounded
  ``timeout(250ms, send)`` / ``Closed`` -> ``NetworkError``) -> the R152
  ok / full-recovers / full-times-out->``BackpressureError`` / closed tests.
* Rust ``call_request`` (671-682, serde + ``oneshot`` + ``WaiterGuard`` +
  outbound + ``rx.await``) -> the R152 returns / propagates-client-error /
  drains-waiter-on-send-failure tests.
* Rust ``call_request_with_deadline`` (693-746, ``timeout(t, rx)`` match ->
  ``TimedOut`` / ``OtherError``) -> the R152 success / times-out /
  wraps-send-failure tests.
* Rust ``call_request_with_timeout`` (684-691, ``DeadlineCallError`` ->
  ``ClientError``) -> the R152 wraps test.
* Rust ``try_alloc_request_id`` (789, ``fetch_add`` + ``RequestId::new``) ->
  the ``c0`` / ``c1`` / ``c2`` monotonic test.
* Rust ``impl Drop for HubConnection`` (853) -> the ``__del__`` stop-signal
  test.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from minimax_code.computer_hub_sdk.auth import PrincipalKey
from minimax_code.computer_hub_sdk.connection import (
    ConnectionConfig,
    HubConnection,
    HubConnectionInner,
    _AtomicCounter,
    _ConnectionIdSlot,
    _EarlyNotifSlot,
    _HelloCaps,
)
from minimax_code.computer_hub_sdk.connection_types import (
    ConnectionTuning,
    ConnHealth,
    ConnKey,
    OtherError,
    TimedOut,
)
from minimax_code.computer_hub_sdk.demux import Demux, mpsc_channel
from minimax_code.computer_hub_sdk.error import BackpressureError, NetworkError
from minimax_code.computer_hub_sdk.refcount import RefCountedSet
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.envelope import (
    JsonRpcIdString,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
)
from minimax_code.tool_protocol.ids import ConnectionId, RequestId, ServerId, SessionId

if TYPE_CHECKING:
    from minimax_code.computer_hub_sdk.connection_types import ReconnectEvent


# ===========================================================================
# Test fakes + helpers.
# ===========================================================================
class _FakeAuth:
    """Minimal stand-in for the ``AuthProvider`` Protocol.

    The accessors under test do not call into the credential, so the methods
    return inert values; the type exists only to satisfy the inner's
    ``credential`` field.
    """

    def current(self) -> None:
        return None

    def principal_key(self) -> PrincipalKey:
        return PrincipalKey(fingerprint="fp-1")

    def identity(self) -> None:
        return None


def _noop_reconnect(_event: ReconnectEvent) -> None:
    """ReconnectCallback stand-in (avoids E731 lambda assignment)."""


def _noop() -> None:
    """Nullary callback stand-in (avoids E731 lambda assignment)."""


def _make_inner(**overrides: object) -> SimpleNamespace:
    """Build a ``HubConnectionInner`` wired to fresh real ``_Sink`` channels.

    Returns a namespace carrying the inner plus every sink / sink-rx pair so a
    test can drive the channel state (close, inspect buffer size) without
    touching private demux internals.
    """
    capacity = int(overrides.pop("outbound_capacity", 8))  # type: ignore[arg-type]
    hello_caps = overrides.pop("hello_caps", None)
    early = overrides.pop("early_notif_rx", None)
    allow_insecure = overrides.pop("allow_insecure_ws", False)
    demux_override = overrides.pop("demux", None)

    outbound_tx, outbound_rx = mpsc_channel(capacity)
    stop_tx, stop_rx = mpsc_channel(8)
    reconnect_tx, reconnect_rx = mpsc_channel(8)

    kwargs: dict[str, object] = dict(
        key=ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1")),
        kind=ConnectionKind.ToolServer,
        credential=_FakeAuth(),
        reconnect_backoff=(1.0, 2.0),
        outbound_tx=outbound_tx,
        demux=demux_override if demux_override is not None else Demux(),
        bound_sessions=RefCountedSet(),
        stop_tx=stop_tx,
        reconnect_tx=reconnect_tx,
        allow_insecure_ws=allow_insecure,  # type: ignore[arg-type]
    )
    if hello_caps is not None:
        kwargs["hello_capabilities"] = _HelloCaps(hello_caps)  # type: ignore[arg-type]
    if early is not None:
        kwargs["early_notif_rx"] = early

    inner = HubConnectionInner(**kwargs)  # type: ignore[arg-type]
    return SimpleNamespace(
        inner=inner,
        outbound_tx=outbound_tx,
        outbound_rx=outbound_rx,
        stop_tx=stop_tx,
        stop_rx=stop_rx,
        reconnect_tx=reconnect_tx,
        reconnect_rx=reconnect_rx,
    )


# ===========================================================================
# ConnectionConfig (lines 376-423).
# ===========================================================================
def test_connection_config_defaults() -> None:
    cfg = ConnectionConfig(
        url="ws://hub",
        credential=_FakeAuth(),
        kind=ConnectionKind.ToolServer,
    )
    assert cfg.url == "ws://hub"
    assert cfg.kind == ConnectionKind.ToolServer
    assert cfg.on_reconnect is None
    assert cfg.on_disconnect is None
    assert cfg.on_connect is None
    assert cfg.server_id is None
    assert cfg.server_description is None
    assert cfg.server_metadata is None
    assert cfg.outbound_buffer is None
    assert isinstance(cfg.tuning, ConnectionTuning)
    assert cfg.alpha_test_key is None
    assert cfg.allow_insecure_ws is False
    assert cfg.on_fatal is None


def test_connection_config_full() -> None:
    cfg = ConnectionConfig(
        url="wss://hub",
        credential=_FakeAuth(),
        kind=ConnectionKind.Harness,
        on_reconnect=_noop_reconnect,
        on_disconnect=_noop,
        on_connect=_noop,
        server_id=ServerId("srv-1"),
        server_description="a one-line description",
        server_metadata={"k": "v"},
        outbound_buffer=128,
        tuning=ConnectionTuning(ws_ping_interval=15.0),
        alpha_test_key="key",
        allow_insecure_ws=True,
    )
    assert cfg.kind == ConnectionKind.Harness
    assert cfg.server_id == ServerId("srv-1")
    assert cfg.server_metadata == {"k": "v"}
    assert cfg.outbound_buffer == 128
    assert cfg.tuning.ws_ping_interval == 15.0
    assert cfg.alpha_test_key == "key"
    assert cfg.allow_insecure_ws is True
    assert cfg.on_reconnect is _noop_reconnect


# ===========================================================================
# HubConnectionInner construction (lines 435-482).
# ===========================================================================
def test_inner_defaults_runtime_state() -> None:
    ctx = _make_inner()
    inner = ctx.inner
    assert isinstance(inner.connection_id, _ConnectionIdSlot)
    assert isinstance(inner.hello_capabilities, _HelloCaps)
    assert isinstance(inner.next_request_id, _AtomicCounter)
    assert isinstance(inner.shutdown, asyncio.Event)
    assert isinstance(inner.health, ConnHealth)
    assert inner.connection_id.get() is None
    assert inner.hello_capabilities.is_empty()
    assert inner.allow_insecure_ws is False
    assert inner.reconnect_backoff == (1.0, 2.0)


def test_inner_optional_overrides() -> None:
    health = ConnHealth()
    counter = _AtomicCounter(100)
    inner = HubConnectionInner(
        key=ConnKey("ws://x", PrincipalKey(fingerprint="fp")),
        kind=ConnectionKind.Harness,
        credential=_FakeAuth(),
        reconnect_backoff=(5.0,),
        outbound_tx=mpsc_channel(4)[0],
        demux=Demux(),
        bound_sessions=RefCountedSet(),
        stop_tx=mpsc_channel(4)[0],
        reconnect_tx=mpsc_channel(4)[0],
        health=health,
        next_request_id=counter,
        allow_insecure_ws=True,
    )
    assert inner.health is health
    assert inner.next_request_id is counter
    assert inner.allow_insecure_ws is True


# ===========================================================================
# Slot helpers (interior-mutability primitives).
# ===========================================================================
def test_atomic_counter_fetch_add_returns_previous() -> None:
    counter = _AtomicCounter()
    assert counter.fetch_add() == 0
    assert counter.fetch_add() == 1
    assert counter.fetch_add(5) == 2
    assert counter.fetch_add() == 7


def test_hello_caps_replace_snapshot_clear() -> None:
    caps = _HelloCaps()
    assert caps.is_empty()
    caps.replace(["a", "b"])
    assert not caps.is_empty()
    assert caps.contains("a")
    assert not caps.contains("c")
    assert caps.snapshot() == ["a", "b"]
    caps.clear()
    assert caps.is_empty()


def test_connection_id_slot_lifecycle() -> None:
    slot = _ConnectionIdSlot()
    assert slot.get() is None
    slot.set(ConnectionId("c1"))
    assert slot.get() == ConnectionId("c1")
    slot.clear()
    assert slot.get() is None


def test_early_notif_slot_take_set() -> None:
    slot = _EarlyNotifSlot()
    assert slot.take() is None
    queue: asyncio.Queue = asyncio.Queue()
    slot.set(queue)
    assert slot.get() is queue
    assert slot.take() is queue
    assert slot.take() is None


# ===========================================================================
# HubConnection identity accessors (lines 596-636).
# ===========================================================================
def test_key_kind_demux_and_repr() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.key() == ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1"))
    assert conn.kind() == ConnectionKind.ToolServer
    assert conn.demux() is ctx.inner.demux
    rendered = repr(conn)
    assert "HubConnection" in rendered
    assert "ws://hub" in rendered


def test_actor_id_stable_and_matches_inner() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.actor_id() == id(ctx.inner)
    # Stable across calls (identity does not change within the instance life).
    assert conn.actor_id() == conn.actor_id()


def test_connection_id_none_then_set() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.connection_id() is None
    ctx.inner.connection_id.set(ConnectionId("conn-1"))
    assert conn.connection_id() == ConnectionId("conn-1")


def test_supports_unknown_when_caps_empty() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    # Empty capability list -> None (indistinguishable from a pre-field server).
    assert conn.supports("session_attach_server") is None


def test_supports_true_false_when_caps_populated() -> None:
    ctx = _make_inner(hello_caps=["session_attach_server", "tools_changed"])
    conn = HubConnection(ctx.inner)
    assert conn.supports("session_attach_server") is True
    assert conn.supports("tools_changed") is True
    assert conn.supports("nonexistent") is False


def test_take_early_notifications_drains_once() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.take_early_notifications() is None
    queue: asyncio.Queue = asyncio.Queue()
    ctx.inner.early_notif_rx.set(queue)
    assert conn.take_early_notifications() is queue
    # Drained: a second take returns None.
    assert conn.take_early_notifications() is None


# ===========================================================================
# Shutdown / reconnect signalling (lines 640-659, 853-857).
# ===========================================================================
def _buffer_size(rx: object) -> int:
    """Items pending in a ``_SinkRx``'s channel (None payload is ambiguous)."""
    return rx._channel.buffer.qsize()  # type: ignore[attr-defined]


def test_force_reconnect_enqueues_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.reconnect_rx) == 0
    conn.force_reconnect()
    assert _buffer_size(ctx.reconnect_rx) == 1


def test_force_reconnect_silent_on_closed_channel() -> None:
    ctx = _make_inner()
    ctx.reconnect_rx.close_channel()
    conn = HubConnection(ctx.inner)
    # Closed channel: the ``let _ =`` discard swallows _MpscClosed silently.
    conn.force_reconnect()


def test_request_shutdown_enqueues_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.stop_rx) == 0
    conn.request_shutdown()
    assert _buffer_size(ctx.stop_rx) == 1


async def test_await_shutdown_resolves_once_event_set() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    # Pre-set so wait() returns promptly (Event has persistent semantics).
    ctx.inner.shutdown.set()
    await asyncio.wait_for(conn.await_shutdown(), timeout=1.0)


def test_del_enqueues_stop_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.stop_rx) == 0
    conn.__del__()  # explicit, mirroring the Rust Drop path.
    assert _buffer_size(ctx.stop_rx) == 1


# ===========================================================================
# Session tracking (lines 663-670, 798-800).
# ===========================================================================
def test_track_untrack_session_refcount() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    s1 = SessionId("s1")
    s2 = SessionId("s2")
    assert conn.bound_session_count() == 0
    # s1 borrowed twice, s2 once -> 2 distinct keys.
    conn.track_session(s1)
    conn.track_session(s1)
    conn.track_session(s2)
    assert conn.bound_session_count() == 2
    # First untrack of s1 leaves refcount 1 -> still tracked.
    conn.untrack_session(s1)
    assert conn.bound_session_count() == 2
    # Second untrack of s1 -> removed.
    conn.untrack_session(s1)
    assert conn.bound_session_count() == 1
    conn.untrack_session(s2)
    assert conn.bound_session_count() == 0


# ===========================================================================
# Outbound + request-id allocation (lines 771-795).
# ===========================================================================
def test_try_send_outbound_ok() -> None:
    ctx = _make_inner(outbound_capacity=4)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("frame-1")
    conn.try_send_outbound("frame-2")
    assert _buffer_size(ctx.outbound_rx) == 2


def test_try_send_outbound_full_raises_backpressure() -> None:
    ctx = _make_inner(outbound_capacity=2)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("f1")
    conn.try_send_outbound("f2")
    with pytest.raises(BackpressureError):
        conn.try_send_outbound("f3")


def test_try_send_outbound_closed_raises_network() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    ctx.outbound_rx.close_channel()
    with pytest.raises(NetworkError):
        conn.try_send_outbound("f1")


def test_try_alloc_request_id_monotonic_c_prefix() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.try_alloc_request_id() == RequestId("c0")
    assert conn.try_alloc_request_id() == RequestId("c1")
    assert conn.try_alloc_request_id() == RequestId("c2")


# ===========================================================================
# Network-outbound methods (R152, lines 671-766).
# ===========================================================================
class _RecordingDemux:
    """Minimal Demux stand-in exposing response-waiter register/take.

    ``call_request`` / ``call_request_with_deadline`` touch the demux only via
    ``register_response_waiter`` + ``take_response_waiter`` (the latter through
    :func:`waiter_guard`), so a real :class:`Demux` is unnecessary: this fake
    parks the future in a dict the test fulfils (``set_result`` /
    ``set_exception``) on its own schedule, exercising every await arm of the
    Rust ``rx.await`` / ``timeout(t, rx)`` translation.
    """

    def __init__(self) -> None:
        self.waiters: dict[str, asyncio.Future] = {}

    def register_response_waiter(self, request_id: str, waiter: asyncio.Future) -> None:
        self.waiters[request_id] = waiter

    def take_response_waiter(self, request_id: str) -> asyncio.Future | None:
        return self.waiters.pop(request_id, None)


def _make_request(request_id: RequestId) -> JsonRpcRequest[dict[str, object]]:
    """Minimal JSON-RPC request with the given id (avoids E731 lambda)."""
    return JsonRpcRequest(
        jsonrpc=JsonRpcVersion(),
        id=JsonRpcIdString.from_request_id(request_id),
        method="ping",
        params={},
    )


def _make_ok_response(request_id: RequestId) -> JsonRpcResponse[dict[str, object]]:
    """Minimal success response with the given id."""
    return JsonRpcResponse.ok(
        JsonRpcIdString.from_request_id(request_id),
        result={"ok": True},
    )


# --- send_outbound (lines 748-766) -----------------------------------------
async def test_send_outbound_ok() -> None:
    ctx = _make_inner(outbound_capacity=4)
    conn = HubConnection(ctx.inner)
    await conn.send_outbound("frame-1")
    assert _buffer_size(ctx.outbound_rx) == 1


async def test_send_outbound_full_recovers_after_bounded_wait() -> None:
    ctx = _make_inner(outbound_capacity=1)
    conn = HubConnection(ctx.inner)
    # Fill the single slot; the next send_outbound must await capacity.
    conn.try_send_outbound("first")
    assert _buffer_size(ctx.outbound_rx) == 1

    async def drain() -> None:
        await asyncio.sleep(0.01)  # let send_outbound park on the full buffer
        await ctx.outbound_rx.recv()

    asyncio.create_task(drain())
    await conn.send_outbound("second")  # recovers once `drain` frees a slot.
    assert _buffer_size(ctx.outbound_rx) == 1


async def test_send_outbound_full_times_out_raises_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minimax_code.computer_hub_sdk.connection as conn_mod

    monkeypatch.setattr(conn_mod, "_OUTBOUND_BACKOFF_WAIT_S", 0.02)
    ctx = _make_inner(outbound_capacity=1)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("first")  # fill the single slot.
    with pytest.raises(BackpressureError):
        await conn.send_outbound("second")  # no drainer -> bounded wait elapses.


async def test_send_outbound_closed_raises_network() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    ctx.outbound_rx.close_channel()
    with pytest.raises(NetworkError):
        await conn.send_outbound("frame")


# --- call_request (lines 671-682) ------------------------------------------
async def test_call_request_returns_response_and_drains_waiter() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    expected = _make_ok_response(rid)

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(expected)

    asyncio.create_task(fulfiller())
    resp = await conn.call_request(rid, _make_request(rid))
    assert resp == expected
    # waiter_guard took the waiter on scope exit -> no leak.
    assert rid not in demux.waiters


async def test_call_request_propagates_client_error_from_waiter() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_exception(NetworkError("server gone"))

    asyncio.create_task(fulfiller())
    with pytest.raises(NetworkError, match="server gone"):
        await conn.call_request(rid, _make_request(rid))
    assert rid not in demux.waiters


async def test_call_request_drains_waiter_on_send_failure() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    ctx.outbound_rx.close_channel()  # send_outbound will raise NetworkError.
    with pytest.raises(NetworkError):
        await conn.call_request(rid, _make_request(rid))
    # waiter registered then drained by waiter_guard despite the send failure.
    assert rid not in demux.waiters


# --- call_request_with_deadline (lines 693-746) ----------------------------
async def test_call_request_with_deadline_success() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    expected = _make_ok_response(rid)

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(expected)

    asyncio.create_task(fulfiller())
    resp = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=1.0)
    assert resp == expected
    assert rid not in demux.waiters


async def test_call_request_with_deadline_times_out() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    # No fulfiller: the deadline elapses before the future resolves. The
    # outcome is a TimedOut value (Rust enum), not a raised exception.
    outcome = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=0.02)
    assert isinstance(outcome, TimedOut)
    assert outcome.timeout == 0.02
    assert rid not in demux.waiters


async def test_call_request_with_deadline_wraps_send_failure_as_other() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    ctx.outbound_rx.close_channel()  # send_outbound raises NetworkError.
    outcome = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=1.0)
    assert isinstance(outcome, OtherError)
    assert isinstance(outcome.error, NetworkError)
    assert rid not in demux.waiters


# --- call_request_with_timeout (lines 684-691) -----------------------------
async def test_call_request_with_timeout_wraps_timed_out_as_client_error() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    # No fulfiller -> TimedOut -> to_client_error -> NetworkError.
    with pytest.raises(NetworkError):
        await conn.call_request_with_timeout(rid, _make_request(rid), timeout=0.02)
    assert rid not in demux.waiters


async def test_call_request_serializes_compact_json_to_outbound() -> None:
    import json as _json

    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(_make_ok_response(rid))

    asyncio.create_task(fulfiller())
    await conn.call_request(rid, _make_request(rid))
    frame = await ctx.outbound_rx.recv()
    decoded = _json.loads(frame)
    assert decoded["jsonrpc"] == "2.0"
    assert decoded["id"] == "c0"
    assert decoded["method"] == "ping"
    assert decoded["params"] == {}
    # Compact separators: no whitespace after ',' or ':'.
    assert ", " not in frame
    assert ": " not in frame
